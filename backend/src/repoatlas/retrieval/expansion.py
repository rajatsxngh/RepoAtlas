"""Graph expansion from seed nodes (RET-6).

From each seed node, follow ``calls``, ``imports``, ``inherits``, ``tested_by``, and
``contains`` edges in both directions, up to ``MAX_HOPS`` hops, over the snapshot's in-memory
adjacency. A node reached over a path of edges gets ``seed score * product of edge
confidences * HOP_DECAY ** hops``. Every node keeps only its best score (over all seeds and
paths, so a strong two-hop path can beat a weak direct edge), with the hop count, seed, and
last edge of that path; seeds themselves are not expanded results. Seeds and edges are visited
in a fixed order and only a strictly better score replaces a kept one, so the result is
deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from repoatlas.graph.model import EdgeKind
from repoatlas.store.cache import SnapshotGraph

MAX_HOPS = 2
HOP_DECAY = 0.5
EXPANSION_KINDS: tuple[EdgeKind, ...] = (
    EdgeKind.CALLS,
    EdgeKind.IMPORTS,
    EdgeKind.INHERITS,
    EdgeKind.TESTED_BY,
    EdgeKind.CONTAINS,
)


Direction = Literal["out", "in"]


@dataclass(frozen=True, slots=True)
class Seed:
    key: str  # the seed's node symbol key
    score: float


@dataclass(frozen=True, slots=True)
class Expanded:
    key: str
    node_id: int  # store row ID (a position for graphs built from a CodeGraph)
    score: float
    hops: int
    seed: str  # key of the seed whose path gave the score
    via: EdgeKind  # kind of the path's last edge
    direction: Direction  # whether that edge was followed forwards or backwards
    confidence: float  # product of the path's edge confidences


def _incident(
    graph: SnapshotGraph, node: int, kinds: Sequence[EdgeKind]
) -> tuple[tuple[Direction, list[int]], tuple[Direction, list[int]]]:
    return (("out", graph.out_edges(node, kinds)), ("in", graph.in_edges(node, kinds)))


def expand(
    graph: SnapshotGraph,
    seeds: Sequence[Seed],
    *,
    kinds: Sequence[EdgeKind] = EXPANSION_KINDS,
    max_hops: int = MAX_HOPS,
    decay: float = HOP_DECAY,
) -> list[Expanded]:
    """Nodes reached from ``seeds``, best score first (ties by key). Seeds whose key is not
    in the graph are ignored."""
    seed_keys = {seed.key for seed in seeds}
    best: dict[int, Expanded] = {}
    for seed in seeds:
        start = graph.index.get(seed.key)
        if start is None:
            continue
        frontier: list[tuple[int, float]] = [(start, 1.0)]  # (node, path confidence)
        for hop in range(1, max_hops + 1):
            reached: dict[int, float] = {}
            for node, confidence in frontier:
                for direction, edges in _incident(graph, node, kinds):
                    for edge in edges:
                        out = direction == "out"
                        neighbor = graph.edge_dst[edge] if out else graph.edge_src[edge]
                        if neighbor == start:
                            continue
                        path = confidence * graph.edge_confidence[edge]
                        score = seed.score * path * decay**hop
                        key = graph.keys[neighbor]
                        if key not in seed_keys:
                            kept = best.get(neighbor)
                            if kept is None or score > kept.score:
                                best[neighbor] = Expanded(
                                    key=key,
                                    node_id=graph.node_ids[neighbor],
                                    score=score,
                                    hops=hop,
                                    seed=seed.key,
                                    via=graph.edge_kind(edge),
                                    direction=direction,
                                    confidence=path,
                                )
                        if path > reached.get(neighbor, 0.0):
                            reached[neighbor] = path
            frontier = sorted(reached.items())
            if not frontier:
                break
    return sorted(best.values(), key=lambda item: (-item.score, item.key))
