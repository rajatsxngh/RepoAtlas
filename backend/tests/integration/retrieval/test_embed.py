"""Embedding stored snapshots through the shared cache (RET-3)."""

from __future__ import annotations

from sqlalchemy import Engine, text

from repoatlas.retrieval.embed import HashEmbedder, embed_chunks
from repoatlas.store.graphstore import GraphStore
from tests.integration.retrieval.conftest import Stored


def distinct_hashes(engine: Engine, snapshot_id: int) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(DISTINCT chunk_hash) FROM chunks WHERE snapshot_id = :s"),
                {"s": snapshot_id},
            ).scalar_one()
        )


def vectors(engine: Engine, model: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM embeddings WHERE model = :m"), {"m": model}
            ).scalar_one()
        )


def test_only_chunks_missing_from_the_cache_are_embedded(stored: Stored, pg_engine: Engine) -> None:
    embedder = HashEmbedder(41)  # a model no other test uses, so the cache starts empty for it
    seen: list[tuple[int, int]] = []
    first = embed_chunks(
        stored.py,
        embedder,
        engine=pg_engine,
        batch_size=4,
        progress=lambda done, total: seen.append((done, total)),
    )
    total = distinct_hashes(pg_engine, stored.py)
    assert (first.model, first.dim, first.chunks, first.embedded, first.cached) == (
        "hash-41",
        41,
        total,
        total,
        0,
    )
    assert seen[-1] == (total, total) and len(seen) == -(-total // 4)
    assert vectors(pg_engine, "hash-41") == total

    again = embed_chunks(stored.py, embedder, engine=pg_engine)
    assert (again.embedded, again.cached) == (0, total)

    other = embed_chunks(stored.ts, embedder, engine=pg_engine)
    assert other.embedded == distinct_hashes(pg_engine, stored.ts)
    assert vectors(pg_engine, "hash-41") == total + other.embedded

    info = GraphStore(pg_engine).snapshot(stored.py)
    assert (info.embedding_model, info.embedding_dim) == ("hash-41", 41)


def test_another_model_gets_its_own_vectors(stored: Stored, pg_engine: Engine) -> None:
    embed_chunks(stored.py, HashEmbedder(43), engine=pg_engine)
    stats = embed_chunks(stored.py, HashEmbedder(47), engine=pg_engine)
    assert stats.embedded == distinct_hashes(pg_engine, stored.py)
    with pg_engine.connect() as connection:
        dims: dict[str, int] = dict(
            connection.execute(
                text(
                    "SELECT model, max(vector_dims(vector)) FROM embeddings "
                    "WHERE model IN ('hash-43', 'hash-47') GROUP BY model"
                )
            ).all()
        )
    assert dims == {"hash-43": 43, "hash-47": 47}
    assert GraphStore(pg_engine).snapshot(stored.py).embedding_model == "hash-47"
