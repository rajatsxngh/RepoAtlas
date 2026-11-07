"""save_snapshot (T2): bulk load, idempotency, shared blobs, failures, and reading graphs back."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text

from repoatlas.graph.model import CodeGraph, Edge, EdgeKind, Owner, Provenance
from repoatlas.store.errors import SnapshotNotFound, SnapshotNotReady
from repoatlas.store.loader import (
    begin_snapshot,
    find_snapshot,
    load_graph,
    mark_failed,
    save_snapshot,
)
from tests.integration.store.helpers import (
    FINGERPRINT,
    GOLDEN,
    SHA_A,
    SHA_B,
    Built,
    build_files,
    fixture_files,
    repo_meta,
    with_owners,
)

ROW_TABLES = ("files", "nodes", "edges", "unresolved_calls", "owners")
pytestmark = pytest.mark.usefixtures("clean_store")


def save(engine: Engine, built: Built, sha: str = SHA_A, **kwargs: Any) -> int:
    meta = kwargs.pop("meta", repo_meta())
    fingerprint = kwargs.pop("fingerprint", FINGERPRINT)
    return save_snapshot(
        meta,
        sha,
        fingerprint,
        built.inventory,
        kwargs.pop("blobs", built.blobs),
        kwargs.pop("graph", built.graph),
        engine=engine,
        **kwargs,
    )


def counts(engine: Engine, snapshot_id: int) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE snapshot_id = :s"), {"s": snapshot_id}
            ).scalar_one()
            for table in ROW_TABLES
        }


def snapshot_row(engine: Engine, snapshot_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT * FROM snapshots WHERE id = :s"), {"s": snapshot_id}
        ).one()


def blob_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM blobs")).scalar_one())


def test_save_snapshot_writes_every_row(pg_engine: Engine, py_small: Built) -> None:
    extra = {"timings": {"parse": 0.5}, "graph_digest": "not the digest"}
    snapshot_id = save(pg_engine, py_small, extra_stats=extra)
    graph = py_small.graph
    assert counts(pg_engine, snapshot_id) == {
        "files": len(py_small.inventory.files),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "unresolved_calls": len(graph.unresolved),
        "owners": 0,
    }
    row = snapshot_row(pg_engine, snapshot_id)
    assert (row.status, row.error, row.commit_sha, row.config_fingerprint) == (
        "ready",
        None,
        SHA_A,
        FINGERPRINT,
    )
    assert row.stats["graph"] == graph.stats.model_dump(mode="json")
    assert row.stats["graph_digest"] == graph.digest()
    assert row.stats["inventory"] == py_small.inventory.stats.model_dump(mode="json")
    assert row.stats["timings"] == {"parse": 0.5}
    indexed_oids = {entry.blob_oid for entry in py_small.inventory.indexed}
    assert row.stats["blobs"]["needed"] == row.stats["blobs"]["stored"] == len(indexed_oids)
    assert blob_count(pg_engine) == len(indexed_oids)
    skipped = [entry for entry in py_small.inventory.files if entry.skip_reason]
    assert skipped  # py_small has vendored and declaration files: rows, but no text
    with pg_engine.connect() as connection:
        stored = set(connection.execute(text("SELECT oid FROM blobs")).scalars())
    assert not {entry.blob_oid for entry in skipped} & stored


@pytest.mark.parametrize("fixture", ["py_small", "ts_small"])
def test_the_graph_read_back_has_the_golden_digest(
    pg_engine: Engine, fixture: str, request: pytest.FixtureRequest
) -> None:
    built: Built = request.getfixturevalue(fixture)
    golden = json.loads((GOLDEN / f"{fixture}.graph.json").read_text(encoding="utf-8"))
    snapshot_id = save(pg_engine, built)
    loaded = load_graph(snapshot_id, engine=pg_engine)
    assert loaded == built.graph
    assert loaded.digest() == built.graph.digest() == golden["digest"]


def test_owners_round_trip_in_utc_whatever_the_session_time_zone(
    pg_url: str, pg_engine: Engine, py_small: Built
) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    owners = {
        "src/shop/cart.py": [
            Owner(
                author_name="Ada Lovelace",
                author_hash="0123456789abcdef",
                commits=3,
                last_commit_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=ist),
                share=0.75,
            ),
            Owner(
                author_name="Grace Hopper",
                author_hash="fedcba9876543210",
                commits=1,
                last_commit_at=datetime(2025, 12, 31, 23, 0, tzinfo=UTC),
                share=0.25,
            ),
        ]
    }
    graph = with_owners(py_small.graph, owners)
    snapshot_id = save(pg_engine, py_small, graph=graph)
    kolkata = create_engine(pg_url, connect_args={"options": "-c timezone=Asia/Kolkata"})
    try:
        loaded = load_graph(snapshot_id, engine=kolkata)
    finally:
        kolkata.dispose()
    assert loaded.owners == graph.owners
    assert loaded.owners["src/shop/cart.py"][0].last_commit_at.tzinfo == UTC
    assert loaded.digest() == graph.digest()
    assert counts(pg_engine, snapshot_id)["owners"] == 2


def test_saving_again_is_idempotent(pg_engine: Engine, py_small: Built) -> None:
    first = save(pg_engine, py_small)
    before = snapshot_row(pg_engine, first)
    second = save(pg_engine, py_small, blobs={})  # nothing is read for a ready snapshot
    assert second == first
    assert snapshot_row(pg_engine, first).updated_at == before.updated_at
    assert counts(pg_engine, first)["nodes"] == len(py_small.graph.nodes)
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM snapshots")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM repos")).scalar_one() == 1


def test_a_new_commit_or_fingerprint_is_a_new_snapshot(pg_engine: Engine, py_small: Built) -> None:
    first = save(pg_engine, py_small)
    other_commit = save(pg_engine, py_small, SHA_B)
    other_fingerprint = save(pg_engine, py_small, fingerprint="e" * 64)
    assert len({first, other_commit, other_fingerprint}) == 3
    assert snapshot_row(pg_engine, other_commit).stats["blobs"]["stored"] == 0


def test_a_second_snapshot_stores_only_changed_files(pg_engine: Engine) -> None:
    files = fixture_files("py_small")
    first = build_files(files)
    changed = dict(files)
    changed["src/shop/cart.py"] = files["src/shop/cart.py"] + b"\n\ndef added():\n    pass\n"
    second = build_files(changed)
    save(pg_engine, first)
    before = blob_count(pg_engine)
    snapshot_id = save(pg_engine, second, SHA_B)
    blobs = snapshot_row(pg_engine, snapshot_id).stats["blobs"]
    assert (blobs["stored"], blobs["shared"]) == (1, blobs["needed"] - 1)
    assert blob_count(pg_engine) == before + 1
    lazy = {oid: data for oid, data in second.blobs.items() if oid not in first.blobs}
    third = save(pg_engine, second, "c" * 40, blobs=lazy)  # unchanged text is never looked up
    assert snapshot_row(pg_engine, third).status == "ready"


def test_the_same_text_in_two_files_is_one_blob(pg_engine: Engine) -> None:
    same = b"def f():\n    return 1\n"
    built = build_files({"a.py": same, "b.py": same})
    snapshot_id = save(pg_engine, built)
    assert blob_count(pg_engine) == 1
    assert snapshot_row(pg_engine, snapshot_id).stats["blobs"]["needed"] == 1


def test_invalid_utf8_and_nul_characters_are_stored_lossy(pg_engine: Engine) -> None:
    invalid = b"name = 'caf\xe9'\nother = 1\n"
    nul = b"x = 1\n" * 2_000 + b"y = '\x00'\nz = 2"  # NUL past the binary sniff window
    clean = b"\xef\xbb\xbfdef f():\r\n    return '\xc3\xa9'\r\n"  # BOM and CRLF are kept
    built = build_files({"invalid.py": invalid, "nul.py": nul, "clean.py": clean})
    assert all(entry.skip_reason is None for entry in built.inventory.files)
    snapshot_id = save(pg_engine, built)
    with pg_engine.connect() as connection:
        stored = {
            row.oid: row
            for row in connection.execute(
                text("SELECT oid, size_bytes, line_count, text, lossy FROM blobs")
            )
        }
    by_path = {entry.path: stored[entry.blob_oid] for entry in built.inventory.files}
    assert by_path["invalid.py"].text == "name = 'caf�'\nother = 1\n"
    assert (by_path["invalid.py"].lossy, by_path["invalid.py"].line_count) == (True, 2)
    assert by_path["nul.py"].text.endswith("y = '�'\nz = 2")
    assert (by_path["nul.py"].lossy, by_path["nul.py"].line_count) == (True, 2_002)
    assert by_path["clean.py"].text == clean.decode("utf-8")
    assert (by_path["clean.py"].lossy, by_path["clean.py"].line_count) == (False, 2)
    assert by_path["clean.py"].size_bytes == len(clean)
    assert snapshot_row(pg_engine, snapshot_id).stats["blobs"]["lossy"] == 2


def test_an_inconsistent_graph_fails_cleanly_and_can_be_retried(
    pg_engine: Engine, py_small: Built
) -> None:
    graph = py_small.graph
    dangling = Edge(
        src=next(iter(graph.nodes)),
        dst="py:src/missing.py:gone:function",
        kind=EdgeKind.CALLS,
        provenance=Provenance.NAME_MATCH,
        confidence=0.5,
    )
    broken = CodeGraph(
        nodes=graph.nodes,
        edges=[*graph.edges, dangling],
        unresolved=graph.unresolved,
        owners=graph.owners,
        stats=graph.stats,
    )
    with pytest.raises(ValueError, match="inconsistent"):
        save(pg_engine, py_small, graph=broken)
    found = find_snapshot("acme", "shop", SHA_A, FINGERPRINT, engine=pg_engine)
    assert found is not None and found.status == "failed"
    row = snapshot_row(pg_engine, found.id)
    assert row.error.startswith("ValueError: The graph is inconsistent")
    assert "py:src/missing.py:gone:function" in row.error
    assert counts(pg_engine, found.id) == dict.fromkeys(ROW_TABLES, 0)  # rolled back
    assert blob_count(pg_engine) == 0

    assert save(pg_engine, py_small) == found.id  # a retry reuses the row
    row = snapshot_row(pg_engine, found.id)
    assert (row.status, row.error) == ("ready", None)


@pytest.mark.parametrize("problem", ["missing", "mismatched"])
def test_missing_or_wrong_blob_bytes_fail_the_snapshot(
    pg_engine: Engine, py_small: Built, problem: str
) -> None:
    oid = next(iter(py_small.blobs))
    blobs = dict(py_small.blobs)
    if problem == "missing":
        del blobs[oid]
    else:
        blobs[oid] = b"not the original bytes"
    with pytest.raises(ValueError, match=oid):
        save(pg_engine, py_small, blobs=blobs)
    found = find_snapshot("acme", "shop", SHA_A, FINGERPRINT, engine=pg_engine)
    assert found is not None and found.status == "failed"
    assert blob_count(pg_engine) == 0


def test_mark_failed_never_downgrades_a_ready_snapshot(pg_engine: Engine, py_small: Built) -> None:
    snapshot_id = save(pg_engine, py_small)
    mark_failed(snapshot_id, RuntimeError("late failure"), engine=pg_engine)
    row = snapshot_row(pg_engine, snapshot_id)
    assert (row.status, row.error) == ("ready", None)


def test_long_errors_are_truncated(pg_engine: Engine) -> None:
    ref = begin_snapshot(repo_meta(), SHA_A, FINGERPRINT, engine=pg_engine)
    mark_failed(ref.id, "x" * 5_000, engine=pg_engine)
    error = snapshot_row(pg_engine, ref.id).error
    assert len(error) == 2_000 and error.endswith("…")


def test_begin_snapshot_marks_building_and_updates_the_repo(pg_engine: Engine) -> None:
    ref = begin_snapshot(repo_meta(branch="main"), SHA_A, FINGERPRINT, engine=pg_engine)
    assert ref.status == "building"
    mark_failed(ref.id, "boom", engine=pg_engine)
    again = begin_snapshot(repo_meta("ACME", "Shop", "trunk"), SHA_A, FINGERPRINT, engine=pg_engine)
    assert again == ref.__class__(id=ref.id, status="building")
    assert snapshot_row(pg_engine, ref.id).error is None
    with pg_engine.connect() as connection:
        repo = connection.execute(text("SELECT owner, name, default_branch FROM repos")).one()
    assert tuple(repo) == ("ACME", "Shop", "trunk")


def test_find_snapshot_ignores_case(pg_engine: Engine, py_small: Built) -> None:
    snapshot_id = save(pg_engine, py_small)
    found = find_snapshot("Acme", "SHOP", SHA_A, FINGERPRINT, engine=pg_engine)
    assert found is not None and (found.id, found.status) == (snapshot_id, "ready")
    assert find_snapshot("acme", "shop", SHA_B, FINGERPRINT, engine=pg_engine) is None


def test_load_graph_needs_a_ready_snapshot(pg_engine: Engine) -> None:
    with pytest.raises(SnapshotNotFound):
        load_graph(999_999, engine=pg_engine)
    ref = begin_snapshot(repo_meta(), SHA_A, FINGERPRINT, engine=pg_engine)
    with pytest.raises(SnapshotNotReady) as raised:
        load_graph(ref.id, engine=pg_engine)
    assert raised.value.status == "building"
