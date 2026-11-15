"""Incremental builds through the store (PRF-2, PRF-3, decision 0024).

Each scripted change set is committed on top of a built snapshot and built again. The new
snapshot must equal a full build of that commit in everything that is compared: the graph's
digest, the chunks (text, lines, nodes, keyword index, and their order), and the embeddings.
The stats say what was reused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from repoatlas.config import Settings
from repoatlas.graph import build as graph_build
from repoatlas.graph.incremental import ReusePlan
from repoatlas.pipeline.build import IncrementalMismatch, build_snapshot, run_build
from repoatlas.pipeline.graph import run_graph
from repoatlas.pipeline.incremental import build_incremental
from repoatlas.retrieval import chunker
from repoatlas.retrieval.keyword import chunk_tsvector
from repoatlas.retrieval.reuse import chunk_stats
from repoatlas.store.errors import SnapshotNotFound
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import load_graph
from repoatlas.store.previous import find_previous
from tests.integration.origin import APP, FILES, TEST, URL, UTIL, Origin, serve_origin
from tests.unit.ingest.gitrepo import Spec

pytestmark = pytest.mark.usefixtures("clean_store")

BASE: dict[str, bytes] = {spec.path: spec.data for spec in FILES} | {
    "src/shop/report.py": b"def render(rows):\n    return [row.strip() for row in rows]\n",
    "src/shop/cart.py": (
        b"from shop.util import helper\n\n\n"
        b"class Cart:\n"
        b"    def total(self):\n"
        b"        return helper()\n\n"
        b"    def add(self, item):\n"
        b"        return self.total()\n"
    ),
}
SOURCES = sorted(path for path in BASE if path.endswith(".py"))  # what the inventory holds
TEXT_CLASS = b"class Text:\n    def strip(self):\n        return self\n"

# name → the files after the change (path → bytes; None removes the path)
CHANGE_SETS: dict[str, dict[str, bytes | None]] = {
    "edit_a_function_body": {
        "src/shop/report.py": b"def render(rows):\n    return list(rows)\n",
    },
    "add_a_file": {
        "src/shop/tax.py": b"from shop.util import helper\n\n\ndef tax():\n    return helper()\n",
    },
    "delete_a_file": {"src/shop/util.py": None},
    "rename_a_file": {"src/shop/util.py": None, "src/shop/helpers.py": UTIL},
    "change_an_import": {
        "src/shop/cart.py": BASE["src/shop/cart.py"].replace(
            b"from shop.util import helper", b"from shop.app import run as helper"
        ),
    },
    "add_a_class_that_a_name_matched_call_finds": {"src/shop/text.py": TEXT_CLASS},
    "edit_a_file_everything_imports": {
        "src/shop/util.py": UTIL + b"\n\ndef other():\n    return helper()\n",
    },
    "several_at_once": {
        "src/shop/util.py": None,
        "src/shop/helpers.py": UTIL,
        "src/shop/app.py": APP.replace(b"shop.util", b"shop.helpers"),
        "src/shop/text.py": TEXT_CLASS,
        "tests/test_app.py": TEST + b"\n\ndef test_again():\n    assert main() == 1\n",
    },
}


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    return serve_origin(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def empty_embeddings(pg_engine: Engine) -> None:
    """The embeddings cache outlives snapshots; these tests count what a build embeds."""
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE embeddings"))


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", database_url=pg_url)


def commit(origin: Origin, files: dict[str, bytes]) -> str:
    origin.head = origin.repo.commit([Spec(path, data) for path, data in sorted(files.items())])
    return origin.head


def apply(files: dict[str, bytes], changes: dict[str, bytes | None]) -> dict[str, bytes]:
    out = dict(files)
    for path, data in changes.items():
        if data is None:
            del out[path]
        else:
            out[path] = data
    return out


def stats(engine: Engine, snapshot_id: int) -> dict[str, Any]:
    return dict(GraphStore(engine).snapshot(snapshot_id).stats)


def stored_chunks(engine: Engine, snapshot_id: int) -> list[tuple[Any, ...]]:
    """The snapshot's chunks in ID order, with their node keys and keyword index."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT n.symbol_key, c.blob_oid, c.path, c.start_line, c.end_line, "
                "c.chunk_hash, c.header, c.text, c.tsv::text AS tsv FROM chunks c "
                "JOIN nodes n ON n.id = c.node_id WHERE c.snapshot_id = :s ORDER BY c.id"
            ),
            {"s": snapshot_id},
        ).all()
    return [tuple(row) for row in rows]


def expected_chunks(engine: Engine, snapshot_id: int) -> list[tuple[Any, ...]]:
    """What a full build stores for the snapshot: its files cut from nothing, in order."""
    chunks = chunker.chunk_snapshot(snapshot_id, engine=engine)
    with engine.connect() as connection:
        index = [
            connection.execute(
                text("SELECT CAST(:doc AS tsvector)::text"), {"doc": chunk_tsvector(chunk)}
            ).scalar_one()
            for chunk in chunks
        ]
    return [
        (
            c.node_key,
            c.blob_oid,
            c.path,
            c.start_line,
            c.end_line,
            c.chunk_hash,
            c.header,
            c.text,
            t,
        )
        for c, t in zip(chunks, index, strict=True)
    ]


def assert_equals_a_full_build(engine: Engine, settings: Settings, snapshot_id: int) -> None:
    graph = load_graph(snapshot_id, engine=engine)
    assert graph.digest() == run_graph(URL, settings).digest
    assert stats(engine, snapshot_id)["graph_digest"] == graph.digest()
    assert stored_chunks(engine, snapshot_id) == expected_chunks(engine, snapshot_id)
    with engine.connect() as connection:
        missing = connection.execute(
            text(
                "SELECT count(*) FROM chunks c WHERE c.snapshot_id = :s AND NOT EXISTS "
                "(SELECT 1 FROM embeddings e WHERE e.chunk_hash = c.chunk_hash)"
            ),
            {"s": snapshot_id},
        ).scalar_one()
    assert missing == 0


@pytest.mark.parametrize("name", sorted(CHANGE_SETS))
def test_a_scripted_change_set_equals_a_full_build(
    name: str, origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    commit(origin, BASE)
    first = build_snapshot(URL, settings, engine=pg_engine)
    assert "incremental" not in stats(pg_engine, first)

    commit(origin, apply(BASE, CHANGE_SETS[name]))
    second = build_snapshot(URL, settings, engine=pg_engine)
    assert second != first
    built = stats(pg_engine, second)
    assert built["incremental"]["previous_snapshot_id"] == first
    assert sum(built["incremental"]["changes"].values()) >= len(SOURCES) - 1
    assert_equals_a_full_build(pg_engine, settings, second)

    # The same commit built with nothing to reuse stores the same graph and chunks.
    other = Settings(**{**settings.model_dump(), "data_dir": settings.data_dir / "full"})
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM snapshots WHERE id = :s"), {"s": first})
    chunks = stored_chunks(pg_engine, second)
    digest = load_graph(second, engine=pg_engine).digest()
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM snapshots WHERE id = :s"), {"s": second})
    full = build_snapshot(URL, other, engine=pg_engine)
    assert "incremental" not in stats(pg_engine, full)
    assert load_graph(full, engine=pg_engine).digest() == digest
    assert stored_chunks(pg_engine, full) == chunks


def test_the_stats_say_what_was_reused(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    commit(origin, BASE)
    first = build_snapshot(URL, settings, engine=pg_engine)
    before = stats(pg_engine, first)
    assert before["parse_cache"] == {"hits": 0, "misses": 6}
    assert (
        before["chunks"]["reused"] == 0 and before["chunks"]["rebuilt"] == before["chunks"]["count"]
    )
    assert before["embeddings"]["cached"] == 0

    commit(origin, apply(BASE, CHANGE_SETS["edit_a_function_body"]))
    second = build_snapshot(URL, settings, engine=pg_engine)
    after = stats(pg_engine, second)
    assert after["incremental"] == {
        "previous_snapshot_id": first,
        "changes": {"unchanged": 5, "modified": 1, "added": 0, "deleted": 0, "renamed": 0},
    }
    assert after["parse_cache"] == {"hits": 5, "misses": 1}
    assert after["link"] == {
        "files_resolved": 1,
        "files_reused": 5,
        "by_rule": {"changed": 1},
    }
    chunks = after["chunks"]
    assert chunks["files_reused"] == 5
    assert chunks["rebuilt"] == 1  # report.py's one function
    assert chunks["reused"] == chunks["count"] - 1
    # Embedding reuse: only the chunk whose text changed is embedded.
    assert after["embeddings"]["embedded"] == 1
    assert after["embeddings"]["cached"] == after["embeddings"]["chunks"] - 1


def test_unchanged_files_are_not_read_or_cut_again(
    origin: Origin, settings: Settings, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(origin, BASE)
    build_snapshot(URL, settings, engine=pg_engine)
    commit(origin, apply(BASE, CHANGE_SETS["edit_a_function_body"]))
    cut: list[str] = []
    real = chunker.chunk_file

    def spy(**kwargs: Any) -> Any:
        cut.append(kwargs["path"])
        return real(**kwargs)

    monkeypatch.setattr(chunker, "chunk_file", spy)
    build_snapshot(URL, settings, engine=pg_engine)
    assert cut == ["src/shop/report.py"]


def test_a_chain_of_incremental_builds(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    files = dict(BASE)
    commit(origin, files)
    previous = build_snapshot(URL, settings, engine=pg_engine)
    for name in ("add_a_file", "rename_a_file", "add_a_class_that_a_name_matched_call_finds"):
        files = apply(files, CHANGE_SETS[name])
        commit(origin, files)
        snapshot = build_snapshot(URL, settings, engine=pg_engine)
        assert stats(pg_engine, snapshot)["incremental"]["previous_snapshot_id"] == previous
        assert_equals_a_full_build(pg_engine, settings, snapshot)
        previous = snapshot


def test_going_back_to_an_earlier_commit_reuses_its_snapshot(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    first_commit = commit(origin, BASE)
    first = build_snapshot(URL, settings, engine=pg_engine)
    commit(origin, apply(BASE, CHANGE_SETS["add_a_file"]))
    second = build_snapshot(URL, settings, engine=pg_engine)
    again = run_build(URL, settings, engine=pg_engine, commit=first_commit)
    assert (again.snapshot_id, again.reused) == (first, True)
    assert find_previous(first, engine=pg_engine) == second


def test_build_incremental_names_the_snapshot_to_reuse(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    commit(origin, BASE)
    first = build_snapshot(URL, settings, engine=pg_engine)
    middle = commit(origin, apply(BASE, CHANGE_SETS["add_a_file"]))
    second = build_snapshot(URL, settings, engine=pg_engine)
    latest = commit(origin, apply(BASE, CHANGE_SETS["several_at_once"]))

    third = build_incremental(first, latest, settings, engine=pg_engine)
    assert third not in (first, second)
    # Built on the snapshot that was named, not on the newest one.
    assert stats(pg_engine, third)["incremental"]["previous_snapshot_id"] == first
    assert_equals_a_full_build(pg_engine, settings, third)
    # A commit that is already built is returned as it is.
    assert build_incremental(first, middle, settings, engine=pg_engine) == second

    with pytest.raises(SnapshotNotFound):
        build_incremental(9_999, latest, settings, engine=pg_engine)
    other = Settings(**{**settings.model_dump(), "owner_commits": 1})
    with pytest.raises(ValueError, match="another configuration"):
        build_incremental(first, latest, other, engine=pg_engine)


def test_incremental_builds_can_be_switched_off(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    commit(origin, BASE)
    build_snapshot(URL, settings, engine=pg_engine)
    commit(origin, apply(BASE, CHANGE_SETS["add_a_file"]))
    off = Settings(**{**settings.model_dump(), "incremental_builds": False})
    second = build_snapshot(URL, off, engine=pg_engine)
    built = stats(pg_engine, second)
    assert "incremental" not in built
    assert built["link"]["files_reused"] == 0
    assert built["chunks"]["reused"] == 0
    assert_equals_a_full_build(pg_engine, settings, second)


def test_a_previous_snapshot_without_records_still_gives_its_chunks(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    """A snapshot built before per-file records existed can't be linked on, but its chunks
    of unchanged files are as good as any."""
    commit(origin, BASE)
    first = build_snapshot(URL, settings, engine=pg_engine)
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM file_records WHERE snapshot_id = :s"), {"s": first})
    commit(origin, apply(BASE, CHANGE_SETS["edit_a_function_body"]))
    second = build_snapshot(URL, settings, engine=pg_engine)
    built = stats(pg_engine, second)
    assert built["link"] == {"files_resolved": 6, "files_reused": 0, "by_rule": {}}
    assert built["chunks"]["files_reused"] == 5
    assert_equals_a_full_build(pg_engine, settings, second)


def test_verification_passes_on_a_correct_build_and_fails_a_wrong_one(
    origin: Origin, settings: Settings, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = Settings(**{**settings.model_dump(), "incremental_verify": True})
    commit(origin, BASE)
    build_snapshot(URL, verify, engine=pg_engine)
    commit(origin, apply(BASE, CHANGE_SETS["add_a_class_that_a_name_matched_call_finds"]))
    second = build_snapshot(URL, verify, engine=pg_engine)
    assert_equals_a_full_build(pg_engine, settings, second)

    # Break the rules: keep every file that has a record. A second class with a strip method
    # makes report.py's call ambiguous, the kept result still says it is not, and the build
    # fails instead of storing a wrong graph.
    def keep_everything(files: Any, _digests: Any, _refs: Any, previous: Any, **_: Any) -> Any:
        known = [parsed.path for parsed in files if parsed.path in previous.records]
        new = {parsed.path: "changed" for parsed in files if parsed.path not in previous.records}
        return ReusePlan(resolve=new, reuse=known)

    monkeypatch.setattr(graph_build, "plan_reuse", keep_everything)
    both: dict[str, bytes | None] = {
        "src/shop/text.py": TEXT_CLASS,
        "src/shop/line.py": TEXT_CLASS.replace(b"Text", b"Line"),
    }
    commit(origin, apply(BASE, both))
    with pytest.raises(IncrementalMismatch):
        build_snapshot(URL, verify, engine=pg_engine)
    with pg_engine.connect() as connection:
        failed = connection.execute(
            text("SELECT status, error FROM snapshots ORDER BY id DESC LIMIT 1")
        ).one()
    assert failed.status == "failed" and "IncrementalMismatch" in failed.error


def test_chunk_stats_from_rows_match_the_chunks(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    """A long function is split into parts; the counts read from the rows are the counts of
    the chunk objects."""
    body = b"".join(b"    value_%d = compute(%d)\n" % (n, n) for n in range(400))
    long_file = b"def long():\n" + body + b"    return value_0\n"
    commit(origin, BASE | {"src/shop/long.py": long_file})
    snapshot = build_snapshot(URL, settings, engine=pg_engine)
    chunks = chunker.chunk_snapshot(snapshot, engine=pg_engine)
    assert sum(1 for chunk in chunks if chunk.part) >= 2
    by_kind: dict[str, int] = {}
    for chunk in chunks:
        by_kind[chunk.kind] = by_kind.get(chunk.kind, 0) + 1
    assert chunk_stats(snapshot, engine=pg_engine) == {
        "count": len(chunks),
        "by_kind": dict(sorted(by_kind.items())),
        "split_parts": sum(1 for chunk in chunks if chunk.part),
        "tokens": sum(chunk.tokens for chunk in chunks),
    }
    # Copied chunks count the same.
    commit(origin, BASE | {"src/shop/long.py": long_file, "src/shop/text.py": TEXT_CLASS})
    second = build_snapshot(URL, settings, engine=pg_engine)
    counted = stats(pg_engine, second)["chunks"]
    again = chunker.chunk_snapshot(second, engine=pg_engine)
    assert counted["split_parts"] == sum(1 for chunk in again if chunk.part)
    assert counted["tokens"] == sum(chunk.tokens for chunk in again)
    assert_equals_a_full_build(pg_engine, settings, second)
