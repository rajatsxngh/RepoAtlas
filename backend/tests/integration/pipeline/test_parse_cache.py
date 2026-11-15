"""The parse cache and the per-file records, through real builds against a local origin."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from repoatlas.config import Settings
from repoatlas.graph.incremental import calls_by_file
from repoatlas.pipeline.build import build_snapshot
from repoatlas.pipeline.graph import run_graph
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import load_graph
from repoatlas.store.previous import load_calls, load_previous, load_records, old_names
from tests.integration.origin import URL, UTIL, Origin, serve_origin
from tests.unit.ingest.gitrepo import Spec

pytestmark = pytest.mark.usefixtures("clean_store")

PARSED = ["src/shop/__init__.py", "src/shop/app.py", "src/shop/util.py", "tests/test_app.py"]


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    return serve_origin(tmp_path, monkeypatch)


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", database_url=pg_url)


def stats(engine: Engine, snapshot_id: int) -> dict[str, object]:
    return dict(GraphStore(engine).snapshot(snapshot_id).stats)


def cache_rows(engine: Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT path, blob_oid FROM parse_cache"))
        return sorted((row.path, row.blob_oid) for row in rows)


def test_a_first_build_parses_everything_and_fills_the_cache(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    snapshot = build_snapshot(URL, settings, engine=pg_engine)
    assert stats(pg_engine, snapshot)["parse_cache"] == {"hits": 0, "misses": 4}
    assert [path for path, _oid in cache_rows(pg_engine)] == PARSED


def test_a_new_commit_parses_only_the_changed_file(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    build_snapshot(URL, settings, engine=pg_engine)
    origin.commit([Spec("src/shop/util.py", UTIL + b"\n\ndef other():\n    return 2\n")])
    second = build_snapshot(URL, settings, engine=pg_engine)
    assert stats(pg_engine, second)["parse_cache"] == {"hits": 3, "misses": 1}
    # Both versions of util.py are cached, each under its own blob.
    assert [path for path, _oid in cache_rows(pg_engine)].count("src/shop/util.py") == 2
    # The graph built from cached parse results is the graph of the commit.
    assert load_graph(second, engine=pg_engine).digest() == run_graph(URL, settings).digest


def test_another_configuration_shares_the_parse_results(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    build_snapshot(URL, settings, engine=pg_engine)
    other = Settings(**{**settings.model_dump(), "owner_commits": 1})  # another fingerprint
    second = build_snapshot(URL, other, engine=pg_engine)
    assert stats(pg_engine, second)["parse_cache"] == {"hits": 4, "misses": 0}


def test_an_unreadable_cache_row_is_parsed_again(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    first = build_snapshot(URL, settings, engine=pg_engine)
    expected = load_graph(first, engine=pg_engine).digest()
    with pg_engine.begin() as connection:
        connection.execute(
            text('UPDATE parse_cache SET parsed = \'{"not": "a parse result"}\' WHERE path = :p'),
            {"p": "src/shop/app.py"},
        )
        connection.execute(
            text(  # another file's result under this file's key
                "UPDATE parse_cache SET parsed = (SELECT parsed FROM parse_cache "
                "WHERE path = 'src/shop/__init__.py') WHERE path = 'tests/test_app.py'"
            )
        )
    other = Settings(**{**settings.model_dump(), "owner_commits": 1})
    second = build_snapshot(URL, other, engine=pg_engine)
    assert stats(pg_engine, second)["parse_cache"] == {"hits": 2, "misses": 2}
    # Owners differ between the two configurations; everything else is the same graph.
    rebuilt = load_graph(second, engine=pg_engine)
    assert rebuilt.model_copy(update={"owners": {}, "stats": None}) == load_graph(
        first, engine=pg_engine
    ).model_copy(update={"owners": {}, "stats": None})
    assert expected


def test_the_cache_can_be_switched_off(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    off = Settings(**{**settings.model_dump(), "parse_cache": False})
    snapshot = build_snapshot(URL, off, engine=pg_engine)
    assert stats(pg_engine, snapshot)["parse_cache"] == {"hits": 0, "misses": 4}
    assert cache_rows(pg_engine) == []
    assert load_graph(snapshot, engine=pg_engine).digest() == run_graph(URL, settings).digest


def test_a_snapshot_keeps_a_record_per_parsed_file(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    snapshot = build_snapshot(URL, settings, engine=pg_engine)
    records = load_records(snapshot, engine=pg_engine)
    assert sorted(records) == PARSED
    graph = load_graph(snapshot, engine=pg_engine)
    counted = sum(sum(record.calls.values()) for record in records.values())
    assert counted == graph.stats.call_sites
    assert records["src/shop/app.py"].calls == {"T1": 2}
    assert stats(pg_engine, snapshot)["link"] == {
        "files_resolved": 4,
        "files_reused": 0,
        "by_rule": {},
    }


def test_stored_call_results_read_back_per_file(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    snapshot = build_snapshot(URL, settings, engine=pg_engine)
    graph = load_graph(snapshot, engine=pg_engine)
    expected = calls_by_file(graph)
    found = load_calls(snapshot, PARSED, engine=pg_engine)
    assert sorted(found) == sorted(expected)
    for path, calls in expected.items():
        assert sorted(map(repr, found[path].edges)) == sorted(map(repr, calls.edges))
        assert sorted(map(repr, found[path].unresolved)) == sorted(map(repr, calls.unresolved))
    assert load_calls(snapshot, ["src/shop/app.py"], engine=pg_engine).keys() == {"src/shop/app.py"}
    assert load_calls(snapshot, [], engine=pg_engine) == {}
    assert old_names(snapshot, ["src/shop/app.py"], engine=pg_engine) == {"main", "run"}


def test_load_previous_needs_records(origin: Origin, settings: Settings, pg_engine: Engine) -> None:
    snapshot = build_snapshot(URL, settings, engine=pg_engine)
    previous = load_previous(
        snapshot, arrived=[], gone=["src/shop/util.py"], modified=[], engine=pg_engine
    )
    assert previous is not None
    assert previous.old_names == {"helper"}
    assert set(previous.load_calls(["tests/test_app.py"])) == {"tests/test_app.py"}
    # A snapshot built before per-file records existed has none.
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM file_records WHERE snapshot_id = :s"), {"s": snapshot})
    assert load_previous(snapshot, arrived=[], gone=[], modified=[], engine=pg_engine) is None
