"""The whole flow over HTTP: request a build, follow its job while a worker runs it, then read
the snapshot through every endpoint."""

from __future__ import annotations

import asyncio
import threading
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from repoatlas.api.app import API_PREFIX
from repoatlas.config import Settings
from repoatlas.jobs.worker import Worker
from tests.integration.origin import HELPER, URL, Origin
from tests.unit.api.helpers import client_for, error_of

pytestmark = pytest.mark.anyio
TIMEOUT_S = 60.0


async def test_build_follow_and_read_a_repository_over_http(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    async with client_for(app) as client:
        accepted = await client.post(f"{API_PREFIX}/repos", json={"url": URL})
        assert accepted.status_code == 202
        snapshot_id, job_id = accepted.json()["snapshot_id"], accepted.json()["job_id"]
        base = f"{API_PREFIX}/snapshots/{snapshot_id}"

        early = await client.get(f"{base}/subgraph")
        assert early.status_code == 409
        assert error_of(early)["details"] | {"error": None} == {
            "snapshot_id": snapshot_id,
            "status": "pending",
            "job_id": job_id,
            "job_status": "queued",
            "error": None,
        }
        pending = (await client.get(base)).json()
        assert (pending["status"], pending["job"]["id"]) == ("pending", job_id)

        worker = Worker(settings, engine=pg_engine, worker_id="e2e", poll_s=0.05)
        thread = threading.Thread(target=worker.run, kwargs={"max_jobs": 1}, daemon=True)
        thread.start()
        seen: list[tuple[str, str | None, float]] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT_S
        while True:
            job = (await client.get(f"{API_PREFIX}/jobs/{job_id}")).json()
            seen.append((job["status"], job["stage"], job["progress"]))
            if job["status"] in ("succeeded", "failed") or loop.time() > deadline:
                break
            await asyncio.sleep(0.02)
        thread.join(TIMEOUT_S)
        assert job["status"] == "succeeded", job
        progress = [value for status, _, value in seen if status == "running"] + [1.0]
        assert progress == sorted(progress)  # never goes backwards
        assert seen[0][0] == "queued" and seen[-1] == ("succeeded", "store", 1.0)

        ready = (await client.get(base)).json()
        assert (ready["status"], ready["commit_sha"], ready["job"]["status"]) == (
            "ready",
            origin.head,
            "succeeded",
        )
        root = (await client.get(f"{base}/subgraph")).json()
        assert [node["key"] for node in root["nodes"]] == ["dir:src", "dir:tests"]
        helper = (await client.get(f"{base}/nodes/{quote(HELPER, safe='')}")).json()
        assert helper["node"]["name"] == "helper"
        assert helper["degree"]["incoming"]["calls"] == 1
        callers = (
            await client.get(
                f"{base}/nodes/{quote(HELPER, safe='')}/neighbors",
                params={"direction": "in", "kinds": ["calls"], "depth": 3},
            )
        ).json()
        assert {hop["node"]["name"]: hop["depth"] for hop in callers["nodes"]} == {
            "run": 1,
            "main": 2,
            "test_main": 3,
        }
        found = (await client.get(f"{base}/search", params={"q": "helpr"})).json()
        assert found["results"][0]["node"]["key"] == HELPER
        path = (
            await client.get(
                f"{base}/path",
                params={"from": "py:tests/test_app.py:test_main:test", "to": HELPER},
            )
        ).json()
        assert [node["name"] for node in path["nodes"]] == ["test_main", "main", "run", "helper"]
        located = (
            await client.get(f"{base}/locate", params={"path": "src/shop/app.py", "line": 9})
        ).json()
        assert located["key"] == "py:src/shop/app.py:run:function"
        code = (
            await client.get(f"{base}/code", params={"path": "src/shop/util.py", "end": 2})
        ).json()
        assert code["text"] == "def helper():\n    return 1"

        again = (await client.post(f"{API_PREFIX}/repos", json={"url": URL})).json()
        assert again == {
            "snapshot_id": snapshot_id,
            "job_id": job_id,
            "reused": True,
            "status": "ready",
        }
    assert origin.fetches == 1
