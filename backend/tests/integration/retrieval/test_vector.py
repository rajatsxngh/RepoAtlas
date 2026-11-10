"""Exact vector search within one snapshot (RET-4)."""

from __future__ import annotations

from sqlalchemy import Engine, text

from repoatlas.retrieval.embed import HashEmbedder, embed_chunks
from repoatlas.retrieval.vector import vector_search
from tests.integration.retrieval.conftest import Stored, store_fixture
from tests.integration.store.helpers import SHA_B

EMBEDDER = HashEmbedder(64)


def snapshot_of(engine: Engine, chunk_ids: list[int]) -> set[int]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text("SELECT DISTINCT snapshot_id FROM chunks WHERE id = ANY(:ids)"),
                {"ids": chunk_ids},
            ).scalars()
        )


def chunk_count(engine: Engine, snapshot_id: int) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM chunks WHERE snapshot_id = :s"), {"s": snapshot_id}
            ).scalar_one()
        )


def key_of(engine: Engine, chunk_id: int) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text(
                    "SELECT n.symbol_key FROM chunks c JOIN nodes n ON n.id = c.node_id "
                    "WHERE c.id = :c"
                ),
                {"c": chunk_id},
            ).scalar_one()
        )


def test_vector_search_never_returns_another_snapshots_chunks(
    stored: Stored, pg_engine: Engine
) -> None:
    # A second snapshot with the very same chunks (so the same cached vectors), and another repo.
    twin = store_fixture(pg_engine, "py_small", owner="twin", sha=SHA_B)
    for snapshot_id in (stored.py, stored.ts, twin):
        embed_chunks(snapshot_id, EMBEDDER, engine=pg_engine)
    query = EMBEDDER.embed_query("add an item to the cart")
    for snapshot_id in (stored.py, stored.ts, twin):
        everything = vector_search(
            snapshot_id, query, model=EMBEDDER.model_name, k=10_000, engine=pg_engine
        )
        assert len(everything) == chunk_count(pg_engine, snapshot_id)  # nothing filtered away
        assert snapshot_of(pg_engine, [hit.chunk_id for hit in everything]) == {snapshot_id}
        top = vector_search(snapshot_id, query, model=EMBEDDER.model_name, k=5, engine=pg_engine)
        assert top == everything[:5]
    py = vector_search(stored.py, query, model=EMBEDDER.model_name, engine=pg_engine)
    again = vector_search(twin, query, model=EMBEDDER.model_name, engine=pg_engine)
    assert [hit.score for hit in py] == [hit.score for hit in again]  # same content, own rows
    assert not {hit.chunk_id for hit in py} & {hit.chunk_id for hit in again}


def test_results_are_ordered_by_cosine_similarity(stored: Stored, pg_engine: Engine) -> None:
    embed_chunks(stored.py, EMBEDDER, engine=pg_engine)
    query = EMBEDDER.embed_query("round money value quantize")
    hits = vector_search(stored.py, query, model=EMBEDDER.model_name, engine=pg_engine)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(-1.0 - 1e-9 <= score <= 1.0 + 1e-9 for score in scores)
    assert key_of(pg_engine, hits[0].chunk_id) == (
        "py:src/shop/utils/helpers.py:round_money:function"
    )


def test_other_models_and_bad_queries_find_nothing(stored: Stored, pg_engine: Engine) -> None:
    embed_chunks(stored.py, EMBEDDER, engine=pg_engine)
    query = EMBEDDER.embed_query("cart")
    assert vector_search(stored.py, query, model="hash-9999", engine=pg_engine) == []
    assert vector_search(stored.py, [], model=EMBEDDER.model_name, engine=pg_engine) == []
    assert vector_search(stored.py, query, model=EMBEDDER.model_name, k=0, engine=pg_engine) == []
    wrong_dim = HashEmbedder(32).embed_query("cart")
    assert vector_search(stored.py, wrong_dim, model=EMBEDDER.model_name, engine=pg_engine) == []
