"""Reciprocal rank fusion and seed selection (RET-6).

Each ranked list (vector, keyword) gives an item ``1 / (RRF_K + rank)`` (ranks from 1); an
item's fused score is the sum over the lists it appears in. Fused results are ordered by
score, then by their best rank in any list, then by ID, so the order is deterministic. The top
``SEEDS`` fused results seed the graph expansion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

RRF_K = 60
SEEDS = 8


@dataclass(frozen=True, slots=True)
class Fused:
    id: int
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # source → rank (from 1)

    @property
    def best_rank(self) -> int:
        return min(self.ranks.values())


def reciprocal_rank_fusion(rankings: Mapping[str, Sequence[int]], *, k: int = RRF_K) -> list[Fused]:
    """Fuse ranked ID lists (best first) by ``Σ 1 / (k + rank)``. An ID listed twice in one
    list counts at its first (best) rank."""
    if k < 0:
        raise ValueError("The RRF constant must not be negative.")
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for source in sorted(rankings):
        for rank, item in enumerate(rankings[source], start=1):
            seen = ranks.setdefault(item, {})
            if source in seen:
                continue
            seen[source] = rank
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    fused = [Fused(item, scores[item], ranks[item]) for item in scores]
    return sorted(fused, key=lambda f: (-f.score, f.best_rank, f.id))


def select_seeds(fused: Sequence[Fused], *, count: int = SEEDS) -> list[Fused]:
    """The ``count`` best fused results (they are already in order)."""
    return list(fused[: max(count, 0)])
