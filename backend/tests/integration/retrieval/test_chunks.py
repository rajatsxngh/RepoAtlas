"""Chunking stored snapshots: text from blobs, the chunks table, and the build stages."""

from __future__ import annotations

from dataclasses import astuple, replace

import pytest
from sqlalchemy import Engine, text

from repoatlas.retrieval.chunker import Chunk, chunk_snapshot
from repoatlas.retrieval.chunkstore import save_chunks
from repoatlas.store.errors import SnapshotNotReady
from repoatlas.store.loader import mark_ready, save_snapshot
from tests.integration.retrieval.conftest import Stored
from tests.integration.store.helpers import FINGERPRINT, SHA_B, build_fixture, repo_meta
from tests.unit.retrieval.helpers import fixture_chunks


def comparable(chunks: list[Chunk] | tuple[Chunk, ...]) -> list[tuple[object, ...]]:
    """Chunks without node IDs (row IDs in the store, positions in memory), in a fixed order."""
    return sorted(astuple(replace(chunk, node_id=0)) for chunk in chunks)


@pytest.mark.parametrize("name", ["py_small", "ts_small"])
def test_stored_snapshots_chunk_like_the_in_memory_graph(
    stored: Stored, pg_engine: Engine, name: str
) -> None:
    snapshot_id = stored.py if name == "py_small" else stored.ts
    chunks = chunk_snapshot(snapshot_id, engine=pg_engine)
    assert comparable(chunks) == comparable(fixture_chunks(name))


def test_chunk_rows_point_at_their_nodes_and_blobs(stored: Stored, pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.path, c.blob_oid, c.start_line, c.end_line, n.symbol_key, n.path AS np, "
                "f.blob_oid AS file_oid FROM chunks c JOIN nodes n ON n.id = c.node_id "
                "JOIN files f ON f.snapshot_id = c.snapshot_id AND f.path = c.path "
                "WHERE c.snapshot_id = :s"
            ),
            {"s": stored.py},
        ).all()
        count = connection.execute(
            text("SELECT count(*) FROM chunks WHERE snapshot_id = :s"), {"s": stored.py}
        ).scalar_one()
    assert len(rows) == count == len(fixture_chunks("py_small"))
    for row in rows:
        assert row.path == row.np and row.blob_oid == row.file_oid
        assert 1 <= row.start_line <= row.end_line


def test_saving_chunks_again_replaces_them(stored: Stored, pg_engine: Engine) -> None:
    chunks = chunk_snapshot(stored.ts, engine=pg_engine)
    save_chunks(stored.ts, chunks, engine=pg_engine)
    save_chunks(stored.ts, chunks, engine=pg_engine)
    with pg_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM chunks WHERE snapshot_id = :s"), {"s": stored.ts}
        ).scalar_one()
    assert count == len(chunks)


@pytest.mark.usefixtures("clean_store")
def test_a_snapshot_saved_as_building_becomes_ready_only_when_marked(pg_engine: Engine) -> None:
    built = build_fixture("py_small")

    def save() -> int:
        return save_snapshot(
            repo_meta("acme", "later"),
            SHA_B,
            FINGERPRINT,
            built.inventory,
            built.blobs,
            built.graph,
            engine=pg_engine,
            ready=False,
        )

    snapshot_id = save()
    save_chunks(snapshot_id, chunk_snapshot(snapshot_id, engine=pg_engine), engine=pg_engine)

    def state() -> tuple[str, int, object]:
        with pg_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, stats, (SELECT count(*) FROM chunks WHERE snapshot_id = s.id) "
                    "AS chunks FROM snapshots s WHERE id = :s"
                ),
                {"s": snapshot_id},
            ).one()
        return row.status, row.chunks, row.stats.get("chunks")

    assert state()[:2] == ("building", len(fixture_chunks("py_small")))
    assert save() == snapshot_id  # saving a building snapshot again replaces it, chunks too
    assert state()[:2] == ("building", 0)
    mark_ready(snapshot_id, engine=pg_engine, extra_stats={"chunks": {"count": 0}})
    assert state() == ("ready", 0, {"count": 0})
    mark_ready(snapshot_id, engine=pg_engine)  # already ready: nothing changes
    with pg_engine.begin() as connection:
        connection.execute(
            text("UPDATE snapshots SET status = 'failed' WHERE id = :s"), {"s": snapshot_id}
        )
    with pytest.raises(SnapshotNotReady):
        mark_ready(snapshot_id, engine=pg_engine)
