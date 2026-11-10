"""POST /repos and GET /jobs/{id}: validation, GitHub checks (recorded responses), snapshot
reuse, and queued builds."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from repoatlas.api.app import API_PREFIX
from repoatlas.config import Settings
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import Stage, register_stage, unregister_stage
from tests.integration.api.conftest import run_worker
from tests.integration.origin import URL, UTIL, Origin, github_json
from tests.unit.api.helpers import client_for, error_of
from tests.unit.ingest.gitrepo import Spec

pytestmark = pytest.mark.anyio
REPOS = f"{API_PREFIX}/repos"
FLASK_API = "/repos/pallets/flask"


def counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        snapshots = connection.execute(text("SELECT count(*) FROM snapshots")).scalar_one()
        jobs = connection.execute(text("SELECT count(*) FROM jobs")).scalar_one()
    return snapshots, jobs


async def test_a_new_repo_is_queued_and_then_reused(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    async with client_for(app) as client:
        first = await client.post(REPOS, json={"url": URL})
        again = await client.post(REPOS, json={"url": "https://github.com/Pallets/Flask.git"})
        job = await client.get(f"{API_PREFIX}/jobs/{first.json()['job_id']}")
    assert first.status_code == 202, first.text
    body = first.json()
    assert set(body) == {"snapshot_id", "job_id", "reused", "status"}
    assert (body["reused"], body["status"]) == (False, "pending")
    assert again.status_code == 202 and again.json() == {**body, "reused": True}
    assert job.status_code == 200
    queued = job.json()
    assert (queued["status"], queued["kind"], queued["snapshot_id"]) == (
        "queued",
        "build",
        body["snapshot_id"],
    )
    assert (queued["stage"], queued["progress"], queued["attempts"], queued["max_attempts"]) == (
        None,
        0.0,
        0,
        3,
    )
    assert queued["error"] is None and queued["finished_at"] is None
    with pg_engine.connect() as connection:
        payload = connection.execute(
            text("SELECT payload FROM jobs WHERE id = :id"), {"id": body["job_id"]}
        ).scalar_one()
    assert payload["url"] == URL and payload["commit"] == origin.head

    run_worker(settings, pg_engine)
    async with client_for(app) as client:
        built = await client.post(REPOS, json={"url": URL})
        finished = await client.get(f"{API_PREFIX}/jobs/{body['job_id']}")
    assert built.json() == {**body, "reused": True, "status": "ready"}
    done = finished.json()
    assert (done["status"], done["progress"], done["stage"], done["attempts"]) == (
        "succeeded",
        1.0,
        "publish",
        1,
    )
    assert done["started_at"] is not None and done["finished_at"] is not None
    assert counts(pg_engine) == (1, 1)
    assert [path.lower() for path in origin.api_paths].count(FLASK_API) == 3  # one per request


async def test_a_failed_build_is_queued_again(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    origin.head = "0" * 40  # GitHub names a commit the origin does not have: fetch fails
    app.state.settings = settings.model_copy(update={"job_max_attempts": 1})
    async with client_for(app) as client:
        first = (await client.post(REPOS, json={"url": URL})).json()
        run_worker(settings, pg_engine)
        failed_job = (await client.get(f"{API_PREFIX}/jobs/{first['job_id']}")).json()
        retry = await client.post(REPOS, json={"url": URL})
    assert failed_job["status"] == "failed" and failed_job["attempts"] == 1
    assert failed_job["error"].startswith("FetchError: ")
    assert retry.status_code == 202
    second = retry.json()
    assert second["snapshot_id"] == first["snapshot_id"] and second["job_id"] != first["job_id"]
    assert (second["reused"], second["status"]) == (False, "pending")
    with pg_engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, error FROM snapshots WHERE id = :s"), {"s": first["snapshot_id"]}
        ).one()
    assert (row.status, row.error) == ("pending", None)


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/pallets/flask",
        "http://github.com/pallets/flask",
        "https://github.com/pallets",
        "https://github.com/pallets/flask/tree/main",
        "https://github.com/pallets/flask?tab=readme",
        "https://github.com/pallets/flask#readme",
        "https://user:token@github.com/pallets/flask",
        "https://github.com:8443/pallets/flask",
        "https://github.com/-pallets/flask",
        "https://github.com/pallets/..",
        "ftp://github.com/pallets/flask",
        "javascript:alert(1)",
        "https://github.com/pallets/fl\u0000ask",
        "   ",
    ],
)
async def test_invalid_urls_are_rejected_before_github_is_asked(
    app: FastAPI, origin: Origin, pg_engine: Engine, url: str
) -> None:
    async with client_for(app) as client:
        response = await client.post(REPOS, json={"url": url})
    assert response.status_code == 422, response.text
    assert error_of(response)["code"] == "invalid_url"
    assert origin.api_paths == [] and counts(pg_engine) == (0, 0)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"url": ""},
        {"url": 5},
        {"url": None},
        {"url": "https://github.com/" + "a" * 600},
        {"url": URL, "commit": "a" * 40},
        ["https://github.com/pallets/flask"],
    ],
)
async def test_malformed_bodies_are_invalid_requests(
    app: FastAPI, origin: Origin, pg_engine: Engine, body: Any
) -> None:
    async with client_for(app) as client:
        response = await client.post(REPOS, json=body)
    assert response.status_code == 422
    assert error_of(response)["code"] == "invalid_request"
    assert origin.api_paths == [] and counts(pg_engine) == (0, 0)


async def test_a_body_that_is_not_json_is_an_invalid_request(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.post(
            REPOS, content=b"url=https://github.com/a/b", headers={"content-type": "text/plain"}
        )
    assert response.status_code == 422 and error_of(response)["code"] == "invalid_request"


async def test_private_repos_are_refused(app: FastAPI, origin: Origin, pg_engine: Engine) -> None:
    origin.repo_json["private"] = True  # what GitHub says when the token can see the repo
    async with client_for(app) as client:
        response = await client.post(REPOS, json={"url": URL})
    assert response.status_code == 403
    error = error_of(response)
    assert error["code"] == "repo_private" and "only public repositories" in error["message"]
    assert counts(pg_engine) == (0, 0)


async def test_unknown_repos_are_not_found(app: FastAPI, origin: Origin, pg_engine: Engine) -> None:
    origin.responses[FLASK_API] = httpx.Response(404, json=github_json("not_found.json"))
    async with client_for(app) as client:
        response = await client.post(REPOS, json={"url": URL})
    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"
    assert error_of(response)["details"] == {"resource": "repo"}
    assert counts(pg_engine) == (0, 0)


async def test_oversize_repos_are_refused(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    origin.repo_json["size"] = settings.max_repo_size_kb + 1
    async with client_for(app) as client:
        response = await client.post(REPOS, json={"url": URL})
    assert response.status_code == 413
    error = error_of(response)
    assert error["code"] == "repo_too_large" and "the limit is" in error["message"]
    assert counts(pg_engine) == (0, 0)


async def test_github_trouble_is_reported_without_queueing(
    app: FastAPI, origin: Origin, pg_engine: Engine
) -> None:
    reset = int(time.time()) + 120
    origin.responses[FLASK_API] = httpx.Response(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)}
    )
    async with client_for(app) as client:
        limited = await client.post(REPOS, json={"url": URL})
        origin.responses[FLASK_API] = httpx.Response(500)
        broken = await client.post(REPOS, json={"url": URL})
    assert limited.status_code == 503
    assert error_of(limited)["code"] == "rate_limited"
    assert error_of(limited)["details"]["upstream"] == "github"
    assert 100 <= int(limited.headers["retry-after"]) <= 121
    assert broken.status_code == 502 and error_of(broken)["code"] == "upstream_unavailable"
    assert counts(pg_engine) == (0, 0)


async def test_concurrent_requests_queue_one_build(
    app: FastAPI, origin: Origin, pg_engine: Engine
) -> None:
    async with client_for(app) as client:
        responses = await asyncio.gather(*(client.post(REPOS, json={"url": URL}) for _ in range(8)))
    bodies = [response.json() for response in responses]
    assert {response.status_code for response in responses} == {202}
    assert len({(body["snapshot_id"], body["job_id"]) for body in bodies}) == 1
    assert sum(1 for body in bodies if not body["reused"]) == 1
    assert counts(pg_engine) == (1, 1)


async def test_a_new_commit_or_pipeline_gets_a_new_snapshot(
    app: FastAPI, origin: Origin, pg_engine: Engine
) -> None:
    def extra(_ctx: BuildContext) -> None:
        pass

    async with client_for(app) as client:
        first = (await client.post(REPOS, json={"url": URL})).json()
        register_stage(Stage("dummy", 1, (), extra))
        try:
            changed = (await client.post(REPOS, json={"url": URL})).json()
        finally:
            unregister_stage("dummy")
        same = (await client.post(REPOS, json={"url": URL})).json()
        origin.commit([Spec("src/shop/util.py", UTIL + b"\n\ndef more():\n    return 2\n")])
        newer = (await client.post(REPOS, json={"url": URL})).json()
    assert changed["snapshot_id"] != first["snapshot_id"] and not changed["reused"]
    assert changed["job_id"] != first["job_id"]
    assert same == {**first, "reused": True}  # the original pipeline again: the first snapshot
    assert newer["snapshot_id"] not in (first["snapshot_id"], changed["snapshot_id"])
    with pg_engine.connect() as connection:
        fingerprints = connection.execute(
            text("SELECT count(DISTINCT config_fingerprint) FROM snapshots")
        ).scalar_one()
    assert fingerprints == 2 and counts(pg_engine) == (3, 3)


@pytest.mark.parametrize("job_id", ["0", "-1", str(2**63), "abc", "1.5"])
async def test_job_ids_are_validated(app: FastAPI, job_id: str) -> None:
    async with client_for(app) as client:
        response = await client.get(f"{API_PREFIX}/jobs/{job_id}")
    assert response.status_code == 422 and error_of(response)["code"] == "invalid_request"


async def test_unknown_jobs_are_not_found(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get(f"{API_PREFIX}/jobs/{2**63 - 1}")
    assert response.status_code == 404
    assert error_of(response)["details"] == {"resource": "job"}
