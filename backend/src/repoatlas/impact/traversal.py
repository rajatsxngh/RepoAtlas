"""Reverse traversal and scoring (IMP-1, IMP-2).

Starting from the changed symbols, the search walks edges against the direction of dependence:

- ``calls`` (caller → callee): from a callee to its callers;
- ``inherits`` (class → base): from a base to its subclasses; and from a method to the methods
  of the same name in its subclasses (``overrides``, a step with the inherits confidences along
  the subclass chain multiplied);
- ``imports`` (file → file): from a file to the files importing it;
- ``tested_by`` (production → test): from production code to its tests (this one forwards);
- ``routes_to`` (route → handler): from a handler to its routes.

Scores: a path's score is the product, over its steps, of ``decay * confidence`` (a start has
1). A node's best score is its best single path's (found best-first, like Dijkstra on products,
so the first time a node is taken off the queue its best score is final). A node's score
combines its parents with noisy-or, ``1 - prod(1 - best(parent) * decay * confidence)``, one term
per distinct parent (parallel edges from one parent count once, at their best). Nodes whose best
score is below ``min_score`` or whose best path is ``max_depth`` steps long are reached but not
expanded. Every node is expanded at most once, so cycles end.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.parsing.symbols import parse_symbol_key
from repoatlas.store.cache import SnapshotGraph

OVERRIDES = "overrides"  # a pseudo edge kind: a base method → the method overriding it
FORWARD_KINDS = frozenset({EdgeKind.TESTED_BY})  # followed src → dst; the others dst → src
TRAVERSABLE_KINDS = (
    EdgeKind.CALLS,
    EdgeKind.INHERITS,
    EdgeKind.IMPORTS,
    EdgeKind.TESTED_BY,
    EdgeKind.ROUTES_TO,
)


@dataclass(frozen=True, slots=True)
class Step:
    """One step of an impact path, in the direction impact spreads: ``src`` is nearer the
    change. ``edge`` is the graph edge position (``None`` for ``overrides`` and ``contains``
    steps, which have no single edge)."""

    src: int
    dst: int
    kind: str
    confidence: float
    edge: int | None = None


@dataclass(slots=True)
class Reach:
    """How the search reached one node."""

    node: int
    best: float  # best single-path score
    depth: int  # steps on the best path
    via: Step | None  # the last step of the best path (``None`` for a start)
    parents: dict[int, float] = field(default_factory=dict)  # parent → its best contribution
    expanded: bool = False

    @property
    def score(self) -> float:
        """Noisy-or over parents (1 for a start)."""
        if self.via is None:
            return 1.0
        remaining = 1.0
        for contribution in self.parents.values():
            remaining *= 1.0 - contribution
        return 1.0 - remaining


@dataclass(frozen=True, slots=True)
class Propagation:
    starts: list[int]
    reached: dict[int, Reach]  # starts included
    expanded: int

    def path_to(self, node: int) -> list[Step]:
        """The best path from a start to ``node`` (empty for a start)."""
        steps: list[Step] = []
        current = self.reached[node]
        seen = {node}
        while current.via is not None:
            steps.append(current.via)
            parent = current.via.src
            if parent in seen:  # cannot happen with products ≤ 1; guards against bad input
                break
            seen.add(parent)
            current = self.reached[parent]
        steps.reverse()
        return steps

    def items(self) -> list[Reach]:
        """Reached nodes that are not starts."""
        starts = set(self.starts)
        return [reach for node, reach in self.reached.items() if node not in starts]


def propagate(
    graph: SnapshotGraph,
    starts: Mapping[int, Iterable[EdgeKind] | None] | Iterable[int],
    *,
    max_depth: int,
    decay: float,
    min_score: float,
    edge_kinds: Iterable[EdgeKind | str] = TRAVERSABLE_KINDS,
) -> Propagation:
    """Spread impact from ``starts`` (node positions). A mapping restricts a start's own first
    expansion to some edge kinds (``None``: all of ``edge_kinds``)."""
    if max_depth < 0:
        raise ValueError("max_depth must not be negative.")
    if not 0 < decay <= 1:
        raise ValueError("decay must be in (0, 1].")
    if not 0 <= min_score <= 1:
        raise ValueError("min_score must be in [0, 1].")
    kinds = tuple(EdgeKind(kind) for kind in edge_kinds)
    start_kinds: dict[int, tuple[EdgeKind, ...]] = {}
    if isinstance(starts, Mapping):
        for node, only in starts.items():
            start_kinds[node] = kinds if only is None else tuple(k for k in kinds if k in only)
    else:
        for node in starts:
            start_kinds[node] = kinds

    reached: dict[int, Reach] = {}
    queue: list[tuple[float, int, str, int]] = []
    for node in sorted(start_kinds, key=lambda n: graph.keys[n]):
        reached[node] = Reach(node=node, best=1.0, depth=0, via=None)
        heapq.heappush(queue, (-1.0, 0, graph.keys[node], node))

    expanded = 0
    while queue:
        negative, depth, _key, node = heapq.heappop(queue)
        reach = reached[node]
        if reach.expanded or -negative < reach.best or depth != reach.depth:
            continue  # a stale queue entry
        reach.expanded = True
        is_start = node in start_kinds
        if not is_start and (reach.best < min_score or reach.depth >= max_depth):
            continue
        if is_start and max_depth == 0:
            continue
        expanded += 1
        for step in _steps(graph, node, start_kinds.get(node, kinds)):
            neighbor = step.dst
            if neighbor in start_kinds or neighbor == node:
                continue
            contribution = reach.best * decay * step.confidence
            other = reached.get(neighbor)
            if other is None:
                other = Reach(node=neighbor, best=0.0, depth=reach.depth + 1, via=None)
                reached[neighbor] = other
            if contribution > other.parents.get(node, 0.0):
                other.parents[node] = contribution
            if other.expanded:
                continue
            if contribution > other.best:
                other.best = contribution
                other.depth = reach.depth + 1
                other.via = step
                heapq.heappush(queue, (-contribution, other.depth, graph.keys[neighbor], neighbor))
    return Propagation(starts=list(start_kinds), reached=reached, expanded=expanded)


def _steps(graph: SnapshotGraph, node: int, kinds: Iterable[EdgeKind]) -> list[Step]:
    """The steps out of ``node`` along the chosen kinds, in a deterministic order."""
    steps: list[Step] = []
    for kind in kinds:
        if kind in FORWARD_KINDS:
            for edge in graph.out_edges(node, [kind]):
                steps.append(
                    Step(node, graph.edge_dst[edge], kind.value, graph.edge_confidence[edge], edge)
                )
        else:
            for edge in graph.in_edges(node, [kind]):
                steps.append(
                    Step(node, graph.edge_src[edge], kind.value, graph.edge_confidence[edge], edge)
                )
        if kind == EdgeKind.INHERITS and graph.kind_of(node) == NodeKind.METHOD:
            steps.extend(overriding_methods(graph, node))
    return steps


def overriding_methods(graph: SnapshotGraph, method: int) -> list[Step]:
    """Methods with the same name in the (transitive) subclasses of ``method``'s class, each
    with the product of the inherits confidences down to its class (the nearest one wins)."""
    owner = graph.parents[method]
    name = symbol_name(graph.keys[method])
    if owner < 0 or graph.kind_of(owner) != NodeKind.CLASS or name is None:
        return []
    found: list[Step] = []
    best: dict[int, float] = {owner: 1.0}
    frontier = [owner]
    while frontier:
        following: list[int] = []
        for cls in frontier:
            for edge in graph.in_edges(cls, [EdgeKind.INHERITS]):
                sub = graph.edge_src[edge]
                confidence = best[cls] * graph.edge_confidence[edge]
                if sub in best:
                    continue
                best[sub] = confidence
                following.append(sub)
                for child in graph.children_of(sub):
                    if graph.kind_of(child) == NodeKind.METHOD and (
                        symbol_name(graph.keys[child]) == name
                    ):
                        found.append(Step(method, child, OVERRIDES, confidence))
        frontier = sorted(following, key=lambda n: graph.keys[n])
    return found


def symbol_name(key: str) -> str | None:
    """The last segment of a symbol key's qualified name (``None`` for other keys)."""
    try:
        return parse_symbol_key(key).qualname.rsplit(".", 1)[-1]
    except ValueError:
        return None
