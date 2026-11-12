from __future__ import annotations

import numpy as np

from repoatlas.bench.schema import AnswerSetTruth, Location, LookupTruth
from repoatlas.evals.embeddings import (
    ChunkRow,
    EmbeddingComparison,
    evaluate_model,
    peak_rss_mb,
    ranked_keys,
    recall_at,
    relevant_keys,
    render_comparison,
    throughput_sample,
)
from repoatlas.evals.registry import EvalItem
from repoatlas.retrieval.embed import HashEmbedder

COMMIT = "c" * 40


def item(qid: str, type_: str, text: str, truth: object, repo: str = "r") -> EvalItem:
    return EvalItem(
        id=qid, type=type_, text=text, repo_id=repo, commit=COMMIT, split="dev", truth=truth
    )


PARSE = "py:src/p.py:parse_config:function"
LOAD = "py:src/p.py:load_file:function"
OTHER = "py:src/o.py:render_page:function"
CHUNKS = {
    "r": [
        ChunkRow(
            chunk_hash="h1", node_key=PARSE, text="def parse_config(text): parse config values"
        ),
        ChunkRow(
            chunk_hash="h2",
            node_key=LOAD,
            text="def load_file(path): return parse_config(read(path))",
        ),
        ChunkRow(
            chunk_hash="h3", node_key=OTHER, text="def render_page(page): html template render"
        ),
        ChunkRow(
            chunk_hash="h1", node_key=PARSE, text="def parse_config(text): parse config values"
        ),
    ]
}
ITEMS = [
    item(
        "r-lookup-001",
        "lookup",
        "Where is parse_config defined?",
        LookupTruth(definition=Location(symbol_key=PARSE, path="src/p.py", line=1)),
    ),
    item(
        "r-callers-001",
        "callers",
        "Which functions call parse_config?",
        AnswerSetTruth(answers=[Location(symbol_key=LOAD, path="src/p.py", line=3)]),
    ),
    item("r-flow-001", "flow", "ignored", None),
    item(
        "x-lookup-001",
        "lookup",
        "no chunks for this repo",
        LookupTruth(definition=Location(symbol_key=OTHER, path="src/o.py", line=1)),
        repo="x",
    ),
]


def test_relevant_keys_come_from_lookup_and_callers_truth() -> None:
    assert relevant_keys(ITEMS[0]) == {PARSE}
    assert relevant_keys(ITEMS[1]) == {LOAD}
    assert relevant_keys(ITEMS[2]) == set()


def test_ranked_keys_deduplicate_nodes() -> None:
    matrix = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32)
    assert ranked_keys(np.array([1.0, 0.0], dtype=np.float32), matrix, ["a", "a", "b"], 5) == [
        "a",
        "b",
    ]
    assert recall_at(["a", "b"], {"b", "c"}, 1) == 0.0
    assert recall_at(["a", "b"], {"b", "c"}, 2) == 0.5
    assert recall_at(["a"], set(), 1) == 0.0


def test_evaluate_model_measures_recall_speed_and_memory() -> None:
    result = evaluate_model(HashEmbedder(64), ITEMS, CHUNKS, ks=(1, 3), sample_size=10)
    assert result.model == "hash-64" and result.dim == 64
    assert result.queries == 3  # lookup and callers only
    assert result.chunks == 4
    assert result.recall_at_k["3"] == 1.0  # three distinct nodes: everything is found by k=3
    assert set(result.recall_by_type) == {"callers", "lookup"}
    assert result.throughput_chunks == 3  # distinct texts
    assert result.chunks_per_second > 0
    assert not result.reused_stored_vectors


def test_stored_vectors_are_reused() -> None:
    embedder = HashEmbedder(64)
    rows = CHUNKS["r"]
    stored = {
        "r": {row.chunk_hash: np.asarray(embedder.embed_documents([row.text])[0]) for row in rows}
    }
    fresh = evaluate_model(embedder, ITEMS, CHUNKS, ks=(1,), sample_size=5)
    reused = evaluate_model(embedder, ITEMS, CHUNKS, stored=stored, ks=(1,), sample_size=5)
    assert reused.reused_stored_vectors
    assert reused.recall_at_k == fresh.recall_at_k


def test_throughput_sample_is_fixed() -> None:
    assert throughput_sample(CHUNKS, 2, 11) == throughput_sample(CHUNKS, 2, 11)
    assert len(throughput_sample(CHUNKS, 99, 11)) == 3


def test_peak_rss_and_rendering() -> None:
    rss = peak_rss_mb()
    assert rss is None or rss > 1
    result = evaluate_model(HashEmbedder(32), ITEMS, CHUNKS, ks=(1, 3), sample_size=3)
    text = render_comparison(
        EmbeddingComparison(
            split="dev", snapshots={"r": 1}, machine="m", cpu_count=4, results=[result]
        )
    )
    assert "| `hash-32` | 32 |" in text and "recall@3" in text
