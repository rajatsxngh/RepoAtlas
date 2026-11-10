"""The retriever: find the code for a question (RET-6, RET-7).

``Retriever.retrieve(snapshot_id, query, mode=…)`` runs, as separate steps:

1. vector search (``vector``, ``hybrid``): the query embedded with the snapshot's model, the
   ``VECTOR_K`` nearest chunks;
2. keyword search (``keyword``, ``hybrid``): the ``KEYWORD_K`` best full-text matches;
3. (``hybrid``) reciprocal rank fusion of both lists, and the top ``SEEDS`` fused chunks' nodes
   as seeds;
4. (``hybrid``) graph expansion from the seeds; every chunk of a reached node becomes a
   candidate with the node's expanded score;
5. rerank: ``max(fused, expanded)`` plus the exact-name bonus (``hybrid`` only; the single
   modes rank by their own score);
6. packing into ``budget_tokens`` (and at most ``k`` chunks).

No LLM is involved. ``RetrievalDebug`` records every step's candidates and scores, so results
can be explained (``repoatlas search --explain``). Chunk text comes from the ``chunks`` table,
which was cut from the store's blobs.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.graph.model import EdgeKind
from repoatlas.retrieval.chunkstore import StoredChunk, load_chunks
from repoatlas.retrieval.embed import Embedder, embedder_for
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.expansion import Seed, expand
from repoatlas.retrieval.fusion import SEEDS, reciprocal_rank_fusion, select_seeds
from repoatlas.retrieval.keyword import build_query, keyword_search
from repoatlas.retrieval.rerank import (
    DEFAULT_BUDGET_TOKENS,
    NAME_BONUS,
    Candidate,
    Source,
    pack,
    query_names,
    rerank,
)
from repoatlas.retrieval.vector import vector_search
from repoatlas.store.errors import SnapshotNotReady, StoreError
from repoatlas.store.graphstore import GraphStore

Mode = Literal["vector", "keyword", "hybrid"]
MODES: tuple[Mode, ...] = get_args(Mode)
VECTOR_K = 40
KEYWORD_K = 40
DEFAULT_K = 20


class RetrievalError(StoreError):
    """The snapshot cannot be searched this way (for example, its embeddings use another
    model than the retriever)."""

    code = "retrieval_unavailable"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class EvidenceItem(_Model):
    ref: EvidenceRef
    text: str
    score: float
    sources: set[Source]
    node_key: str

    @field_serializer("sources")
    def _sorted_sources(self, sources: set[Source]) -> list[str]:
        return sorted(sources)


class Hit(_Model):
    """One chunk in one step's list."""

    chunk_id: int
    ref: str  # compact evidence reference
    node_key: str
    score: float
    rank: int


class FusedHit(_Model):
    chunk_id: int
    ref: str
    node_key: str
    score: float
    ranks: dict[str, int]


class ExpandedHit(_Model):
    node_key: str
    score: float
    hops: int
    seed: str
    via: EdgeKind
    direction: Literal["out", "in"]
    confidence: float
    chunks: int  # how many chunks the node has


class RankedHit(_Model):
    chunk_id: int
    ref: str
    node_key: str
    score: float
    fused: float
    expanded: float
    bonus: float
    sources: list[str]
    selected: bool
    skipped: str | None = None  # contained, budget, or limit


class RetrievalDebug(_Model):
    query: str
    mode: Mode
    model: str | None  # the embedding model used for the query (None without vector search)
    keyword_query: str | None
    budget_tokens: int
    k: int | None
    vector: list[Hit] = []
    keyword: list[Hit] = []
    fused: list[FusedHit] = []
    seeds: list[str] = []
    expanded: list[ExpandedHit] = []
    ranked: list[RankedHit] = []
    tokens: int = 0
    notes: list[str] = []
    timings_ms: dict[str, float] = Field(default_factory=dict)


class RetrievalResult(_Model):
    evidence: list[EvidenceItem]
    debug: RetrievalDebug


class Retriever:
    """Hybrid retrieval over one store. ``embedder`` embeds queries; it must be the model the
    snapshot was embedded with (``vector`` and ``hybrid`` modes check)."""

    def __init__(self, store: GraphStore, embedder: Embedder | None = None) -> None:
        self.store = store
        self.embedder = embedder

    @classmethod
    def from_settings(cls, settings: Settings, engine: Engine | None = None) -> Retriever:
        return cls(GraphStore.from_settings(settings, engine), embedder_for(settings))

    @property
    def engine(self) -> Engine:
        return self.store.engine

    def retrieve(
        self,
        snapshot_id: int,
        query: str,
        mode: Mode = "hybrid",
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        k: int | None = DEFAULT_K,
    ) -> RetrievalResult:
        if mode not in MODES:
            raise ValueError(f"The mode must be one of {', '.join(MODES)}.")
        if budget_tokens < 1 or (k is not None and k < 1):
            raise ValueError("The budget and k must be positive.")
        timings: dict[str, float] = {}
        notes: list[str] = []
        info = self.store.snapshot(snapshot_id)
        if info.status != "ready":
            raise SnapshotNotReady(f"Snapshot {snapshot_id} is {info.status}.", info.status)

        vector_ids: list[tuple[int, float]] = []
        model: str | None = None
        if mode in ("vector", "hybrid"):
            with _timed(timings, "vector"):
                model = self._model_for(info.embedding_model, mode, notes)
                if model is not None and self.embedder is not None:
                    vector = self.embedder.embed_query(query)
                    hits = vector_search(
                        snapshot_id, vector, model=model, k=VECTOR_K, engine=self.engine
                    )
                    vector_ids = [(hit.chunk_id, hit.score) for hit in hits]
        keyword_ids: list[tuple[int, float]] = []
        if mode in ("keyword", "hybrid"):
            with _timed(timings, "keyword"):
                hits_k = keyword_search(snapshot_id, query, k=KEYWORD_K, engine=self.engine)
                keyword_ids = [(hit.chunk_id, hit.score) for hit in hits_k]

        with _timed(timings, "load"):
            chunks = load_chunks(
                snapshot_id, ids=[i for i, _ in vector_ids + keyword_ids], engine=self.engine
            )

        fused_scores: dict[int, float] = {}
        fused_hits: list[FusedHit] = []
        seeds: list[Seed] = []
        expanded_hits: list[ExpandedHit] = []
        expanded_by_node: dict[int, float] = {}
        if mode == "hybrid":
            with _timed(timings, "fusion"):
                fused = reciprocal_rank_fusion(
                    {
                        "vector": [i for i, _ in vector_ids],
                        "keyword": [i for i, _ in keyword_ids],
                    }
                )
                fused_scores = {item.id: item.score for item in fused}
                fused_hits = [
                    FusedHit(
                        chunk_id=item.id,
                        ref=chunks[item.id].ref.to_compact(),
                        node_key=chunks[item.id].node_key,
                        score=item.score,
                        ranks=item.ranks,
                    )
                    for item in fused
                ]
                seeds = _seeds([(chunks[item.id], item.score) for item in select_seeds(fused)])
            with _timed(timings, "expansion"):
                reached = expand(self.store.graph(snapshot_id), seeds) if seeds else []
                expanded_by_node = {item.node_id: item.score for item in reached}
                extra = load_chunks(snapshot_id, node_ids=expanded_by_node, engine=self.engine)
                chunks.update(extra)
                per_node: dict[int, int] = {}
                for stored in extra.values():
                    per_node[stored.node_id] = per_node.get(stored.node_id, 0) + 1
                expanded_hits = [
                    ExpandedHit(
                        node_key=item.key,
                        score=item.score,
                        hops=item.hops,
                        seed=item.seed,
                        via=item.via,
                        direction=item.direction,
                        confidence=item.confidence,
                        chunks=per_node.get(item.node_id, 0),
                    )
                    for item in reached
                ]
        elif mode == "vector":
            fused_scores = dict(vector_ids)
        else:
            fused_scores = dict(keyword_ids)

        with _timed(timings, "rerank"):
            in_vector = {i for i, _ in vector_ids}
            in_keyword = {i for i, _ in keyword_ids}
            candidates = []
            for chunk_id, stored in chunks.items():
                sources: set[Source] = set()
                if chunk_id in in_vector:
                    sources.add("vector")
                if chunk_id in in_keyword:
                    sources.add("keyword")
                if stored.node_id in expanded_by_node:
                    sources.add("graph")
                candidates.append(
                    Candidate(
                        chunk_id=chunk_id,
                        node_key=stored.node_key,
                        node_name=stored.node_name,
                        qualified_name=stored.qualified_name,
                        kind=stored.kind,
                        ref=stored.ref,
                        tokens=stored.tokens,
                        fused=fused_scores.get(chunk_id, 0.0),
                        expanded=expanded_by_node.get(stored.node_id, 0.0),
                        sources=frozenset(sources),
                    )
                )
            bonus = NAME_BONUS if mode == "hybrid" else 0.0
            ranked = rerank(candidates, query_names(query), bonus=bonus)
        with _timed(timings, "pack"):
            packed = pack(ranked, budget_tokens=budget_tokens, k=k)

        selected = {item.candidate.chunk_id for item in packed.selected}
        skipped = {item.candidate.chunk_id: reason for item, reason in packed.skipped}
        evidence = [
            EvidenceItem(
                ref=item.candidate.ref,
                text=chunks[item.candidate.chunk_id].text,
                score=item.score,
                sources=set(item.candidate.sources),
                node_key=item.candidate.node_key,
            )
            for item in packed.selected
        ]
        debug = RetrievalDebug(
            query=query,
            mode=mode,
            model=model,
            keyword_query=build_query(query) if mode != "vector" else None,
            budget_tokens=budget_tokens,
            k=k,
            vector=_hits(vector_ids, chunks),
            keyword=_hits(keyword_ids, chunks),
            fused=fused_hits,
            seeds=[seed.key for seed in seeds],
            expanded=expanded_hits,
            ranked=[
                RankedHit(
                    chunk_id=item.candidate.chunk_id,
                    ref=item.candidate.ref.to_compact(),
                    node_key=item.candidate.node_key,
                    score=item.score,
                    fused=item.candidate.fused,
                    expanded=item.candidate.expanded,
                    bonus=item.bonus,
                    sources=sorted(item.candidate.sources),
                    selected=item.candidate.chunk_id in selected,
                    skipped=skipped.get(item.candidate.chunk_id),
                )
                for item in ranked
            ],
            tokens=packed.tokens,
            notes=notes,
            timings_ms=timings,
        )
        return RetrievalResult(evidence=evidence, debug=debug)

    def _model_for(self, snapshot_model: str | None, mode: Mode, notes: list[str]) -> str | None:
        """The embedding model to search with, or ``None`` (noted) when there is none."""
        if snapshot_model is None:
            if mode == "vector":
                raise RetrievalError("The snapshot has no embeddings; rebuild it to search.")
            notes.append("The snapshot has no embeddings; vector search was skipped.")
            return None
        if self.embedder is None:
            if mode == "vector":
                raise RetrievalError("This retriever has no embedder for vector search.")
            notes.append("No embedder is configured; vector search was skipped.")
            return None
        if self.embedder.model_name != snapshot_model:
            raise RetrievalError(
                f"The snapshot was embedded with {snapshot_model}, but this retriever embeds "
                f"queries with {self.embedder.model_name}. Set REPOATLAS_EMBEDDING_MODEL to "
                "the snapshot's model."
            )
        return snapshot_model


def _seeds(fused: list[tuple[StoredChunk, float]]) -> list[Seed]:
    """One seed per node, with the best score of its chunks among the top fused chunks."""
    best: dict[str, float] = {}
    for stored, score in fused:
        best[stored.node_key] = max(best.get(stored.node_key, 0.0), score)
    return [Seed(key, score) for key, score in best.items()][:SEEDS]


def _hits(ranked: list[tuple[int, float]], chunks: dict[int, StoredChunk]) -> list[Hit]:
    return [
        Hit(
            chunk_id=chunk_id,
            ref=chunks[chunk_id].ref.to_compact(),
            node_key=chunks[chunk_id].node_key,
            score=score,
            rank=rank,
        )
        for rank, (chunk_id, score) in enumerate(ranked, start=1)
    ]


@contextmanager
def _timed(timings: dict[str, float], step: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[step] = round((time.perf_counter() - started) * 1000, 2)
