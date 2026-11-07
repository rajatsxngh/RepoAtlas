"""Collecting edges: repeated evidence for the same edge collapses into one edge."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from repoatlas.graph.model import Edge, EdgeKind, Provenance
from repoatlas.parsing.models import Range

EdgeId = tuple[str, str, EdgeKind, Provenance]


@dataclass
class _Pending:
    confidence: float
    site: Range | None
    meta: dict[str, JsonValue]
    count: int
    counted: bool


def _position(site: Range | None) -> tuple[float, float, float, float]:
    if site is None:
        return (float("inf"),) * 4
    return (site.start_line, site.start_col, site.end_line, site.end_col)


class EdgeSet:
    """Edges keyed by (src, dst, kind, provenance).

    Adding an edge that already exists keeps the higher confidence, with the earliest site (and
    the meta) of the evidence that gave it; ``counted`` edges record in ``meta.count`` how many
    times they were added (for example, how many call sites link the same pair).
    """

    def __init__(self) -> None:
        self._items: dict[EdgeId, _Pending] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, edge_id: object) -> bool:
        return edge_id in self._items

    def add(
        self,
        src: str,
        dst: str,
        kind: EdgeKind,
        provenance: Provenance,
        confidence: float,
        *,
        site: Range | None = None,
        meta: dict[str, JsonValue] | None = None,
        counted: bool = False,
    ) -> None:
        edge_id = (src, dst, kind, provenance)
        current = self._items.get(edge_id)
        if current is None:
            self._items[edge_id] = _Pending(confidence, site, dict(meta or {}), 1, counted)
            return
        current.count += 1
        better = confidence > current.confidence
        same_and_earlier = confidence == current.confidence and _position(site) < _position(
            current.site
        )
        if better or same_and_earlier:
            current.confidence, current.site, current.meta = confidence, site, dict(meta or {})

    def edges(self, kind: EdgeKind | None = None) -> list[Edge]:
        """The collected edges (only those of ``kind``, when given)."""
        result: list[Edge] = []
        for (src, dst, edge_kind, provenance), pending in self._items.items():
            if kind is not None and edge_kind != kind:
                continue
            meta = {**pending.meta, "count": pending.count} if pending.counted else pending.meta
            result.append(
                Edge(
                    src=src,
                    dst=dst,
                    kind=edge_kind,
                    provenance=provenance,
                    confidence=pending.confidence,
                    site=pending.site,
                    meta=meta,
                )
            )
        return result
