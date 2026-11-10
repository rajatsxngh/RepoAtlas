"""The retriever's modes and steps on the stored fixture repos (RET-6, RET-7)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from repoatlas.retrieval.embed import HashEmbedder, embed_chunks
from repoatlas.retrieval.retriever import RetrievalError, Retriever
from repoatlas.store.errors import SnapshotNotReady
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import save_snapshot
from tests.integration.retrieval.conftest import Stored, store_fixture
from tests.integration.store.helpers import FINGERPRINT, SHA_B, build_fixture, repo_meta

EMBEDDER = HashEmbedder(256)
ROUND = "py:src/shop/utils/helpers.py:round_money:function"
ADD = "py:src/shop/cart.py:Cart.add:method"


@pytest.fixture(scope="module")
def retriever(stored: Stored, pg_engine: Engine) -> Retriever:
    for snapshot_id in (stored.py, stored.ts):
        embed_chunks(snapshot_id, EMBEDDER, engine=pg_engine)
    return Retriever(GraphStore(pg_engine), EMBEDDER)


def keys(result: object) -> list[str]:
    return [item.node_key for item in result.evidence]  # type: ignore[attr-defined]


def test_hybrid_retrieval_finds_the_symbol_with_exact_evidence(
    stored: Stored, retriever: Retriever, pg_engine: Engine
) -> None:
    result = retriever.retrieve(stored.py, "how is money rounded? round_money")
    assert keys(result)[0] == ROUND
    top = result.evidence[0]
    assert top.ref.symbol_key == ROUND and top.ref.path == "src/shop/utils/helpers.py"
    assert top.sources >= {"vector", "keyword"}
    code = GraphStore(pg_engine).read_code(
        stored.py, top.ref.path, top.ref.start_line, top.ref.end_line
    )
    assert code.blob_oid == top.ref.blob_oid and code.text == top.text
    scores = [item.score for item in result.evidence]
    assert scores == sorted(scores, reverse=True)


def test_graph_expansion_brings_in_neighbors(stored: Stored, retriever: Retriever) -> None:
    result = retriever.retrieve(stored.py, "Cart.add")
    found = {item.node_key: item for item in result.evidence}
    assert keys(result)[0] == ADD  # exact-name bonus
    assert "graph" in found[ROUND].sources  # Cart.add calls round_money
    assert any(hit.node_key == ROUND and hit.seed == ADD for hit in result.debug.expanded)
    assert ADD in result.debug.seeds


def test_single_modes_use_only_their_own_source(stored: Stored, retriever: Retriever) -> None:
    keyword = retriever.retrieve(stored.py, "round_money", mode="keyword")
    assert keyword.evidence and all(item.sources == {"keyword"} for item in keyword.evidence)
    assert (
        keyword.debug.vector == [] and keyword.debug.expanded == [] and keyword.debug.model is None
    )
    vector = retriever.retrieve(stored.py, "round_money", mode="vector")
    assert vector.evidence and all(item.sources == {"vector"} for item in vector.evidence)
    assert vector.debug.keyword == [] and vector.debug.model == "hash-256"
    assert [hit.score for hit in vector.debug.vector][: len(vector.evidence)] == [
        item.score for item in vector.evidence
    ]


def test_the_budget_and_k_bound_the_evidence(stored: Stored, retriever: Retriever) -> None:
    small = retriever.retrieve(stored.py, "cart items price", budget_tokens=120, k=None)
    assert sum(hit.chunk_id > 0 for hit in small.debug.ranked if hit.selected) == len(
        small.evidence
    )
    assert small.debug.tokens <= 120
    assert any(hit.skipped == "budget" for hit in small.debug.ranked)
    capped = retriever.retrieve(stored.py, "cart items price", k=3)
    assert len(capped.evidence) == 3
    with pytest.raises(ValueError):
        retriever.retrieve(stored.py, "cart", budget_tokens=0)
    with pytest.raises(ValueError):
        retriever.retrieve(stored.py, "cart", mode="fuzzy")  # type: ignore[arg-type]


def test_debug_records_every_step_and_results_are_deterministic(
    stored: Stored, retriever: Retriever
) -> None:
    first = retriever.retrieve(stored.ts, "format the cart total price")
    again = retriever.retrieve(stored.ts, "format the cart total price")
    assert first.evidence == again.evidence
    strip = {"timings_ms"}
    assert first.debug.model_dump(exclude=strip) == again.debug.model_dump(exclude=strip)
    debug = first.debug
    assert debug.vector and debug.keyword and debug.fused and debug.seeds and debug.ranked
    assert set(debug.timings_ms) == {
        "vector",
        "keyword",
        "load",
        "fusion",
        "expansion",
        "rerank",
        "pack",
    }
    assert debug.keyword_query is not None and "price" in debug.keyword_query
    assert len(debug.seeds) <= 8
    for item in first.evidence:
        assert item.ref.path.startswith("src/")  # everything comes from ts_small


def test_a_snapshot_without_embeddings_skips_vector_search(
    stored: Stored, pg_engine: Engine
) -> None:
    plain = store_fixture(pg_engine, "ts_small", owner="plain", sha=SHA_B)  # chunked, not embedded
    retriever = Retriever(GraphStore(pg_engine), EMBEDDER)
    result = retriever.retrieve(plain, "createServer")
    assert keys(result)[0] == "ts:src/server.ts:createServer:function"
    assert result.debug.vector == [] and result.debug.notes
    with pytest.raises(RetrievalError, match="no embeddings"):
        retriever.retrieve(plain, "createServer", mode="vector")
    assert Retriever(GraphStore(pg_engine)).retrieve(plain, "createServer").evidence


def test_another_embedding_model_is_refused(stored: Stored, retriever: Retriever) -> None:
    other = Retriever(retriever.store, HashEmbedder(64))
    with pytest.raises(RetrievalError, match="embedded with hash-256"):
        other.retrieve(stored.py, "cart")


def test_snapshots_that_are_not_ready_are_refused(pg_engine: Engine, stored: Stored) -> None:
    built = build_fixture("py_small")
    building = save_snapshot(
        repo_meta("acme", "unready"),
        SHA_B,
        FINGERPRINT,
        built.inventory,
        built.blobs,
        built.graph,
        engine=pg_engine,
        ready=False,
    )
    with pytest.raises(SnapshotNotReady):
        Retriever(GraphStore(pg_engine), EMBEDDER).retrieve(building, "cart")
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM snapshots WHERE id = :s"), {"s": building})
