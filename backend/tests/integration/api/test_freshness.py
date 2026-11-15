"""GET /snapshots/{id}/freshness, and the rebuild it leads to (PRF-4): a snapshot behind its
repository's default branch is reported stale, and building the repository again gives a
fresh snapshot, built incrementally."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from repoatlas.api.app import API_PREFIX
from repoatlas.config import Settings
from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.freshness import FreshnessChecker, remote_head
from repoatlas.ingest.github import RepoRef
from tests.integration.api.conftest import run_worker
from tests.integration.origin import URL, UTIL, Origin
from tests.unit.api.helpers import client_for, error_of
from tests.unit.ingest.gitrepo import Spec

pytestmark = pytest.mark.anyio
REPOS = f"{API_PREFIX}/repos"


def freshness_url(snapshot_id: int) -> str:
    return f"{API_PREFIX}/snapshots/{snapshot_id}/freshness"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def against(origin: Origin, clock: Clock, compared: list[tuple[str, str]]) -> FreshnessChecker:
    """A checker that asks the local origin with a real ``git ls-remote``."""

    def head_of(ref: RepoRef, branch: str) -> str:
        assert ref.key == "pallets/flask"
        return remote_head(origin.repo.url, branch)

    def behind(_ref: RepoRef, base: str, head: str) -> int | None:
        compared.append((base, head))
        return 2

    return FreshnessChecker(head_of, behind, clock=clock)


async def build(app: FastAPI, settings: Settings, engine: Engine) -> int:
    async with client_for(app) as client:
        accepted = await client.post(REPOS, json={"url": URL})
    assert accepted.status_code == 202, accepted.text
    if not accepted.json()["reused"]:
        run_worker(settings, engine)
    return int(accepted.json()["snapshot_id"])


async def test_a_stale_snapshot_is_reported_and_a_rebuild_is_fresh(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    clock = Clock()
    compared: list[tuple[str, str]] = []
    app.state.freshness = against(origin, clock, compared)
    first_head = origin.head
    first = await build(app, settings, pg_engine)

    async with client_for(app) as client:
        fresh = await client.get(freshness_url(first))
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert set(body) == {
        "snapshot_commit",
        "remote_head",
        "stale",
        "commits_behind",
        "default_branch",
        "checked_at",
    }
    assert (body["snapshot_commit"], body["remote_head"]) == (first_head, first_head)
    assert (body["stale"], body["commits_behind"], body["default_branch"]) == (False, 0, "main")

    # The repository moves on. The answer is reused for five minutes, then asked again.
    origin.commit([Spec("src/shop/util.py", UTIL + b"\n\ndef other():\n    return 2\n")])
    async with client_for(app) as client:
        cached = await client.get(freshness_url(first))
        clock.now += 301
        stale = await client.get(freshness_url(first))
    assert cached.json() == body
    assert stale.status_code == 200
    assert stale.json() | {"checked_at": None} == {
        "snapshot_commit": first_head,
        "remote_head": origin.head,
        "stale": True,
        "commits_behind": 2,
        "default_branch": "main",
        "checked_at": None,
    }
    assert compared == [(first_head, origin.head)]

    # What the banner's button does: build the repository again.
    second = await build(app, settings, pg_engine)
    assert second != first
    async with client_for(app) as client:
        rebuilt = await client.get(freshness_url(second))
        snapshot = await client.get(f"{API_PREFIX}/snapshots/{second}")
        old = await client.get(freshness_url(first))
    assert (rebuilt.json()["stale"], rebuilt.json()["commits_behind"]) == (False, 0)
    assert rebuilt.json()["snapshot_commit"] == origin.head
    stats = snapshot.json()["stats"]
    assert stats["incremental"]["previous_snapshot_id"] == first  # the rebuild was incremental
    assert stats["incremental"]["changes"]["modified"] == 1
    assert stats["parse_cache"] == {"hits": 3, "misses": 1}
    assert old.json()["stale"] is True  # the old snapshot is still behind


async def test_freshness_of_an_unknown_snapshot_is_404(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get(freshness_url(424242))
    assert response.status_code == 404
    assert error_of(response)["details"]["resource"] == "snapshot"


async def test_freshness_is_502_when_the_repository_cannot_be_asked(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    first = await build(app, settings, pg_engine)

    def unreachable(_ref: RepoRef, _branch: str) -> str:
        raise FetchError("git ls-remote failed: could not resolve host: github.com")

    app.state.freshness = FreshnessChecker(unreachable, lambda *_: None)
    async with client_for(app) as client:
        response = await client.get(freshness_url(first))
    assert response.status_code == 502
    assert error_of(response)["code"] == "upstream_unavailable"


async def test_freshness_works_while_a_snapshot_is_still_building(
    app: FastAPI, origin: Origin, settings: Settings
) -> None:
    app.state.freshness = against(origin, Clock(), [])
    async with client_for(app) as client:
        accepted = await client.post(REPOS, json={"url": URL})
        response = await client.get(freshness_url(accepted.json()["snapshot_id"]))
    assert response.status_code == 200
    assert response.json()["stale"] is False
