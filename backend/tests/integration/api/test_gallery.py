"""The launch gallery (LCH-2): ``precompute`` builds the list's repositories at their pinned
commits through the ordinary build, and ``GET /gallery`` shows the ones that are ready."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.config import Settings
from repoatlas.gallery.listing import GalleryEntry, GalleryList
from repoatlas.gallery.precompute import PrecomputeResult, precompute
from repoatlas.gallery.store import read_gallery
from repoatlas.ingest.errors import RepoNotFound
from repoatlas.pipeline.build import BuildResult, run_build
from tests.integration.origin import URL, Origin
from tests.unit.api.helpers import api_settings, client_for

pytestmark = pytest.mark.anyio
GALLERY = f"{API_PREFIX}/gallery"


def entry(url: str, commit: str, **changes: Any) -> GalleryEntry:
    values: dict[str, Any] = {
        "url": url,
        "commit": commit,
        "title": url.rsplit("/", 1)[-1].title(),
        "description": "A description written in the list.",
        "language": "Python",
        "license": "BSD-3-Clause",
    }
    return GalleryEntry(**{**values, **changes})


def listing(*entries: GalleryEntry) -> GalleryList:
    return GalleryList(version=1, repos=entries)


def flask_for_all(url: str, settings: Settings, *, engine: Engine, commit: str) -> BuildResult:
    """A build that maps every URL to the local origin's repository (the only one served)."""
    return run_build(URL, settings, engine=engine, commit=commit)


async def test_precompute_builds_the_pinned_commit_and_the_gallery_shows_it(
    app: FastAPI, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    pinned = origin.head
    origin.commit([])  # the default branch moves on; the gallery stays at its pin
    wanted = listing(entry(URL, pinned, title="Flask", benchmark=True))
    seen: list[PrecomputeResult] = []

    first = precompute(wanted, settings, engine=pg_engine, report=seen.append)
    again = precompute(wanted, settings, engine=pg_engine)

    assert [(r.repo, r.commit, r.ok, r.reused) for r in first] == [
        ("pallets/flask", pinned, True, False)
    ]
    assert seen == first
    assert again[0].reused and again[0].snapshot_id == first[0].snapshot_id
    assert origin.fetches == 1  # the second run fetched nothing

    async with client_for(app) as client:
        response = await client.get(GALLERY)
    assert response.status_code == 200, response.text
    (card,) = response.json()["entries"]
    assert card == {
        "snapshot_id": first[0].snapshot_id,
        "repo": "pallets/flask",
        "commit_sha": pinned,
        "title": "Flask",
        "description": "A description written in the list.",
        "language": "Python",
        "license": "BSD-3-Clause",
        "benchmark": True,
        "files": card["files"],
        "nodes": card["nodes"],
        "edges": card["edges"],
    }
    assert card["files"] == 4 and card["nodes"] > card["files"] and card["edges"] > 0

    # The card's snapshot is ready: it opens without a build.
    async with client_for(app) as client:
        snapshot = await client.get(f"{API_PREFIX}/snapshots/{card['snapshot_id']}")
        subgraph = await client.get(f"{API_PREFIX}/snapshots/{card['snapshot_id']}/subgraph")
    assert snapshot.json()["status"] == "ready"
    assert subgraph.status_code == 200


async def test_the_gallery_is_empty_when_nothing_was_precomputed(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get(GALLERY)

    assert response.status_code == 200
    assert response.json() == {"entries": []}


async def test_the_gallery_needs_the_token_behind_the_access_gate(
    pg_url: str, tmp_path: Any
) -> None:
    gated = create_app(
        api_settings(database_url=pg_url, data_dir=tmp_path / "data", access_token="s3cret-token")
    )
    try:
        async with client_for(gated) as client:
            refused = await client.get(GALLERY)
            allowed = await client.get(GALLERY, headers={"Authorization": "Bearer s3cret-token"})
    finally:
        gated.state.engine.dispose()

    assert refused.status_code == 401
    assert allowed.status_code == 200


def test_cards_keep_the_order_of_the_list_and_text_is_updated(
    origin: Origin, settings: Settings, pg_engine: Engine, clean_store: None
) -> None:
    one = entry("https://github.com/acme/one", origin.head)
    two = entry("https://github.com/acme/two", origin.head)
    precompute(listing(one, two), settings, engine=pg_engine, build=flask_for_all)
    assert [item.title for item in read_gallery(engine=pg_engine)] == ["One", "Two"]

    renamed = entry("https://github.com/acme/one", origin.head, title="The first")
    precompute(listing(two, renamed), settings, engine=pg_engine, build=flask_for_all)

    assert [item.title for item in read_gallery(engine=pg_engine)] == ["Two", "The first"]


def test_a_repo_that_left_the_list_leaves_the_gallery_unless_only_some_were_built(
    origin: Origin, settings: Settings, pg_engine: Engine, clean_store: None
) -> None:
    one = entry("https://github.com/acme/one", origin.head)
    two = entry("https://github.com/acme/two", origin.head)
    precompute(listing(one, two), settings, engine=pg_engine, build=flask_for_all)

    only_two = precompute(
        listing(two), settings, engine=pg_engine, build=flask_for_all, only=["ACME/Two"]
    )
    assert [result.repo for result in only_two] == ["acme/two"]
    assert len(read_gallery(engine=pg_engine)) == 2  # a partial run removes nothing

    precompute(listing(two), settings, engine=pg_engine, build=flask_for_all)
    assert [item.title for item in read_gallery(engine=pg_engine)] == ["Two"]


def test_one_entry_failing_does_not_stop_the_others(
    origin: Origin, settings: Settings, pg_engine: Engine, clean_store: None
) -> None:
    def build(url: str, settings: Settings, *, engine: Engine, commit: str) -> BuildResult:
        if url.endswith("/gone"):
            raise RepoNotFound("github.com/acme/gone does not exist.")
        return flask_for_all(url, settings, engine=engine, commit=commit)

    wanted = listing(
        entry("https://github.com/acme/gone", origin.head),
        entry("https://github.com/acme/here", origin.head),
    )

    results = precompute(wanted, settings, engine=pg_engine, build=build)

    assert [(r.repo, r.ok, r.snapshot_id is None) for r in results] == [
        ("acme/gone", False, True),
        ("acme/here", True, False),
    ]
    assert results[0].error == "github.com/acme/gone does not exist."
    assert [item.title for item in read_gallery(engine=pg_engine)] == ["Here"]


def test_a_snapshot_that_is_not_ready_is_not_shown_and_a_deleted_one_takes_its_row(
    origin: Origin, settings: Settings, pg_engine: Engine, clean_store: None
) -> None:
    (result,) = precompute(
        listing(entry(URL, origin.head)), settings, engine=pg_engine, build=flask_for_all
    )
    with pg_engine.begin() as connection:
        connection.execute(
            text("UPDATE snapshots SET status = 'failed' WHERE id = :id"),
            {"id": result.snapshot_id},
        )
    assert read_gallery(engine=pg_engine) == []

    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM snapshots WHERE id = :id"), {"id": result.snapshot_id})
        rows = connection.execute(text("SELECT count(*) FROM gallery")).scalar_one()
    assert rows == 0
