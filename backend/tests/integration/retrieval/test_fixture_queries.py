"""Labeled fixture queries reach their expected symbols (M06, T10).

In CI this runs with the hash embedder, which checks the keyword and graph paths (and that
vector search runs end to end). With the real model,

    REPOATLAS_TEST_EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-code \\
    REPOATLAS_RETRIEVAL_REPORT=retrieval-report.json \\
    uv run pytest tests/integration/retrieval/test_fixture_queries.py

checks the semantic path too and writes a per-mode, per-language report.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Literal

import pytest
import yaml
from pydantic import BaseModel
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.retrieval.embed import HashEmbedder, embedder_for
from repoatlas.retrieval.embed import embed_chunks as embed_snapshot
from repoatlas.retrieval.retriever import MODES, Mode, Retriever
from repoatlas.store.graphstore import GraphStore
from tests.conftest import TEST_EMBEDDING_MODEL
from tests.integration.retrieval.conftest import Stored

QUERIES = Path(__file__).resolve().parents[2] / "fixtures" / "retrieval_queries.yaml"
TOP = 5
HASHED = TEST_EMBEDDING_MODEL.startswith("hash-")
# With the hash embedder only identifier overlap counts; the real model must reach 80%.
MIN_HYBRID = 0.70 if HASHED else 0.80


class Query(BaseModel):
    id: str
    repo: Literal["py_small", "ts_small"]
    style: Literal["natural", "identifier"]
    query: str
    expected: list[str]


def load_queries() -> list[Query]:
    data = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
    return [Query.model_validate(item) for item in data["queries"]]


def test_the_query_file_is_valid_and_names_real_symbols() -> None:
    from tests.unit.retrieval.helpers import fixture_graph

    queries = load_queries()
    assert len({query.id for query in queries}) == len(queries) >= 20
    for query in queries:
        nodes = fixture_graph(query.repo).nodes
        missing = [key for key in query.expected if key not in nodes]
        assert not missing, (query.id, missing)


@pytest.fixture(scope="module")
def retriever(
    stored: Stored, pg_engine: Engine, tmp_path_factory: pytest.TempPathFactory
) -> Retriever:
    settings = Settings(
        _env_file=None,
        embedding_model=TEST_EMBEDDING_MODEL,
        data_dir=Path(os.environ.get("REPOATLAS_DATA_DIR", tmp_path_factory.mktemp("data"))),
    )
    embedder = HashEmbedder(int(TEST_EMBEDDING_MODEL[5:])) if HASHED else embedder_for(settings)
    for snapshot_id in (stored.py, stored.ts):
        embed_snapshot(snapshot_id, embedder, engine=pg_engine)
    return Retriever(GraphStore(pg_engine), embedder)


def rank_of(retriever: Retriever, snapshot_id: int, query: Query, mode: Mode) -> int | None:
    result = retriever.retrieve(snapshot_id, query.query, mode=mode, k=TOP)
    for rank, item in enumerate(result.evidence, start=1):
        if item.node_key in query.expected:
            return rank
    return None


def test_fixture_queries_find_their_symbols(stored: Stored, retriever: Retriever) -> None:
    queries = load_queries()
    snapshots = {"py_small": stored.py, "ts_small": stored.ts}
    ranks: dict[str, dict[str, int | None]] = {mode: {} for mode in MODES}
    for mode in MODES:
        for query in queries:
            ranks[mode][query.id] = rank_of(retriever, snapshots[query.repo], query, mode)

    def rate(mode: str, keep: object = None) -> float:
        chosen = [q for q in queries if keep is None or keep in (q.repo, q.style)]
        return sum(ranks[mode][q.id] is not None for q in chosen) / len(chosen)

    report = {
        "model": TEST_EMBEDDING_MODEL,
        "top": TOP,
        "queries": len(queries),
        "found": {
            mode: {
                "all": rate(mode),
                "py_small": rate(mode, "py_small"),
                "ts_small": rate(mode, "ts_small"),
                "natural": rate(mode, "natural"),
                "identifier": rate(mode, "identifier"),
            }
            for mode in MODES
        },
        "ranks": ranks,
    }
    if target := os.environ.get("REPOATLAS_RETRIEVAL_REPORT"):
        Path(target).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    misses = defaultdict(list)
    for mode in MODES:
        misses[mode] = [query_id for query_id, rank in ranks[mode].items() if rank is None]
    assert rate("hybrid") >= MIN_HYBRID, dict(misses)
    # Identifier queries never need embeddings.
    assert rate("keyword", "identifier") == 1.0, misses["keyword"]
    assert rate("hybrid", "identifier") == 1.0, misses["hybrid"]
    # Hybrid is at least as good as either source alone.
    assert rate("hybrid") >= max(rate("keyword"), rate("vector") if not HASHED else 0.0)
