"""Exact nearest-neighbor search within one snapshot (RET-4, decision 0009).

The snapshot's chunks are filtered first, joined to their vectors for one model, and ordered by
cosine distance. There is no approximate index: one filtered, exact scan per query never
returns another snapshot's chunks and never loses results to a post-scan filter. Ties are
broken by chunk ID, so results are deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, text

from repoatlas.db.engine import get_engine
from repoatlas.retrieval.embed import vector_literal


@dataclass(frozen=True, slots=True)
class VectorHit:
    chunk_id: int
    score: float  # cosine similarity, 1 - cosine distance


def vector_search(
    snapshot_id: int,
    query: Sequence[float],
    *,
    model: str,
    k: int = 40,
    engine: Engine | None = None,
) -> list[VectorHit]:
    """The snapshot's ``k`` chunks closest to ``query`` among vectors of ``model``."""
    if k < 1 or not query:
        return []
    engine = engine or get_engine()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.id, 1 - (e.vector <=> CAST(:q AS vector)) AS score FROM chunks c "
                "JOIN embeddings e ON e.chunk_hash = c.chunk_hash AND e.model = :model "
                "WHERE c.snapshot_id = :snapshot AND e.dim = :dim "
                "ORDER BY e.vector <=> CAST(:q AS vector), c.id LIMIT :k"
            ),
            {
                "q": vector_literal(query),
                "model": model,
                "snapshot": snapshot_id,
                "dim": len(query),
                "k": k,
            },
        ).all()
    return [VectorHit(chunk_id=row.id, score=float(row.score)) for row in rows]
