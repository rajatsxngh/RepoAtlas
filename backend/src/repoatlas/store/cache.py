"""In-memory adjacency per snapshot, kept in an LRU cache with a memory cap (STO-5, decision 0006).

A ``SnapshotGraph`` holds a snapshot's topology only: node keys, kinds, and containment
parents, and edges as parallel arrays (source, destination, kind, confidence, row ID), indexed
per edge kind in both directions (compressed sparse rows). Names, ranges, and meta stay in
Postgres; callers fetch them only for the nodes and edges a query returns.

Nodes are numbered in symbol-key order and edges are sorted by (source, destination, kind), so
every adjacency list is in key order and every result is deterministic. Traversals keep a
visited set, so self-loops, mutual recursion, and long cycles all end.
"""

from __future__ import annotations

import sys
import threading
from array import array
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from repoatlas.config import Settings
from repoatlas.graph.model import CodeGraph, EdgeKind, NodeKind

Direction = Literal["in", "out", "both"]
DIRECTIONS: tuple[Direction, ...] = ("in", "out", "both")
NODE_KINDS: tuple[NodeKind, ...] = tuple(NodeKind)
EDGE_KINDS: tuple[EdgeKind, ...] = tuple(EdgeKind)
DEFAULT_GRAPH_CACHE_MB = 512
_NODE_CODE = {kind: code for code, kind in enumerate(NODE_KINDS)}
_EDGE_CODE = {kind: code for code, kind in enumerate(EDGE_KINDS)}
_ARRAY_OVERHEAD = 80  # bytes of object header per array, roughly


@dataclass(frozen=True, slots=True)
class NodeRecord:
    """What the cache needs to know about a node (``id`` is its store row ID)."""

    id: int
    key: str
    kind: NodeKind
    parent_id: int | None


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    id: int
    src_id: int
    dst_id: int
    kind: EdgeKind
    confidence: float


@dataclass(frozen=True, slots=True)
class Reached:
    """A breadth-first traversal: node index → depth (the start has depth 0), and the edges
    followed, each once, in the order they were found."""

    start: int
    depths: dict[int, int]
    edges: list[int]
    truncated: bool


@dataclass(frozen=True, slots=True)
class PathFound:
    """``edges[i]`` links ``nodes[i]`` and ``nodes[i + 1]``; when ``directed`` is false some
    edges point backwards along the path."""

    nodes: list[int]
    edges: list[int]
    directed: bool


class SnapshotGraph:
    """The adjacency of one snapshot. Node and edge positions ("indices") are internal to it;
    ``keys``, ``node_ids``, and ``edge_ids`` translate them back."""

    def __init__(
        self, snapshot_id: int, nodes: Iterable[NodeRecord], edges: Iterable[EdgeRecord]
    ) -> None:
        self.snapshot_id = snapshot_id
        ordered = sorted(nodes, key=lambda node: node.key)
        self.keys: list[str] = [node.key for node in ordered]
        self.index: dict[str, int] = {key: position for position, key in enumerate(self.keys)}
        if len(self.index) != len(self.keys):
            raise ValueError(f"Snapshot {snapshot_id} has duplicate node keys.")
        by_id = {node.id: position for position, node in enumerate(ordered)}

        def position_of(node_id: int, what: str) -> int:
            position = by_id.get(node_id)
            if position is None:
                raise ValueError(f"Snapshot {snapshot_id}: {what} {node_id} is not a node.")
            return position

        self.node_ids = array("q", (node.id for node in ordered))
        self.node_kinds = bytes(_NODE_CODE[node.kind] for node in ordered)
        self.parents = array(
            "i",
            (
                -1 if node.parent_id is None else position_of(node.parent_id, "parent")
                for node in ordered
            ),
        )

        resolved = sorted(
            (
                position_of(edge.src_id, "edge source"),
                position_of(edge.dst_id, "edge destination"),
                _EDGE_CODE[edge.kind],
                edge.id,
                edge.confidence,
            )
            for edge in edges
        )
        self.edge_src = array("i", (row[0] for row in resolved))
        self.edge_dst = array("i", (row[1] for row in resolved))
        self.edge_kinds = bytes(row[2] for row in resolved)
        self.edge_ids = array("q", (row[3] for row in resolved))
        self.edge_confidence = array("d", (row[4] for row in resolved))

        by_kind: list[list[int]] = [[] for _ in EDGE_KINDS]
        for position, code in enumerate(self.edge_kinds):
            by_kind[code].append(position)
        count = len(self.keys)
        self._out = [_csr(count, self.edge_src, chosen) for chosen in by_kind]
        self._in = [_csr(count, self.edge_dst, chosen) for chosen in by_kind]
        self.extra_bytes = 0  # memoized results add to the estimate
        self._base_bytes = self._measure()

    @classmethod
    def from_code_graph(cls, graph: CodeGraph, snapshot_id: int = 0) -> SnapshotGraph:
        """Build from an in-memory graph, numbering nodes and edges from 1 (tests, tools)."""
        ids = {key: position for position, key in enumerate(graph.nodes, start=1)}
        nodes = [
            NodeRecord(
                id=ids[key],
                key=key,
                kind=node.kind,
                parent_id=None if node.parent_key is None else ids[node.parent_key],
            )
            for key, node in graph.nodes.items()
        ]
        edges = [
            EdgeRecord(
                id=position,
                src_id=ids[edge.src],
                dst_id=ids[edge.dst],
                kind=edge.kind,
                confidence=edge.confidence,
            )
            for position, edge in enumerate(graph.edges, start=1)
        ]
        return cls(snapshot_id, nodes, edges)

    # --- size -----------------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self.keys)

    @property
    def edge_count(self) -> int:
        return len(self.edge_ids)

    @property
    def estimated_bytes(self) -> int:
        return self._base_bytes + self.extra_bytes

    def _measure(self) -> int:
        size = sys.getsizeof(self.keys) + sum(sys.getsizeof(key) for key in self.keys)
        size += sys.getsizeof(self.index)
        arrays: list[array[int] | array[float]] = [
            self.node_ids,
            self.parents,
            self.edge_src,
            self.edge_dst,
            self.edge_ids,
            self.edge_confidence,
        ]
        for offsets, positions in (*self._out, *self._in):
            arrays += [offsets, positions]
        size += sum(_ARRAY_OVERHEAD + len(values) * values.itemsize for values in arrays)
        return size + len(self.node_kinds) + len(self.edge_kinds)

    # --- lookups --------------------------------------------------------------------------

    def kind_of(self, node: int) -> NodeKind:
        return NODE_KINDS[self.node_kinds[node]]

    def edge_kind(self, edge: int) -> EdgeKind:
        return EDGE_KINDS[self.edge_kinds[edge]]

    def out_edges(self, node: int, kinds: Iterable[EdgeKind] | None = None) -> list[int]:
        """Edges leaving ``node`` (all kinds by default), by kind then destination key."""
        return [edge for edge, _ in self._incident(node, "out", _codes(kinds))]

    def in_edges(self, node: int, kinds: Iterable[EdgeKind] | None = None) -> list[int]:
        """Edges arriving at ``node``, by kind then source key."""
        return [edge for edge, _ in self._incident(node, "in", _codes(kinds))]

    def _incident(
        self, node: int, direction: Direction, codes: Sequence[int]
    ) -> Iterator[tuple[int, int]]:
        """(edge, neighbor) pairs around ``node``: out-edges first, then in-edges."""
        if direction != "in":
            for code in codes:
                offsets, positions = self._out[code]
                for edge in positions[offsets[node] : offsets[node + 1]]:
                    yield edge, self.edge_dst[edge]
        if direction != "out":
            for code in codes:
                offsets, positions = self._in[code]
                for edge in positions[offsets[node] : offsets[node + 1]]:
                    yield edge, self.edge_src[edge]

    # --- traversals -----------------------------------------------------------------------

    def traverse(
        self,
        start: int,
        *,
        direction: Direction,
        kinds: Iterable[EdgeKind] | None = None,
        depth: int = 1,
        max_nodes: int | None = None,
    ) -> Reached:
        """Breadth-first search from ``start`` up to ``depth`` hops over edges of ``kinds``.

        Each node is reached once, at its smallest depth; an edge is listed when the search
        follows it, including edges back to nodes already reached (cycles), but not edges among
        nodes at the last depth. After ``max_nodes`` nodes (the start excluded), no new node is
        added and ``truncated`` is set.
        """
        if direction not in DIRECTIONS:
            raise ValueError(f"Unknown direction {direction!r}.")
        codes = _codes(kinds)
        depths = {start: 0}
        edges: list[int] = []
        followed: set[int] = set()
        truncated = False
        frontier = [start]
        for level in range(1, depth + 1):
            reached: list[int] = []
            for node in frontier:
                for edge, neighbor in self._incident(node, direction, codes):
                    if neighbor not in depths:
                        if max_nodes is not None and len(depths) > max_nodes:
                            truncated = True
                            continue
                        depths[neighbor] = level
                        reached.append(neighbor)
                    if edge not in followed:
                        followed.add(edge)
                        edges.append(edge)
            if not reached:
                break
            frontier = sorted(reached)  # key order, so truncation is deterministic too
        return Reached(start=start, depths=depths, edges=edges, truncated=truncated)

    def shortest_path(
        self, src: int, dst: int, kinds: Iterable[EdgeKind] | None = None
    ) -> PathFound | None:
        """A shortest path following edge directions; failing that, one ignoring them."""
        if src == dst:
            return PathFound(nodes=[src], edges=[], directed=True)
        codes = _codes(kinds)
        searches: tuple[tuple[Direction, bool], ...] = (("out", True), ("both", False))
        for direction, directed in searches:
            found = self._search(src, dst, direction, codes)
            if found is not None:
                return PathFound(nodes=found[0], edges=found[1], directed=directed)
        return None

    def _search(
        self, src: int, dst: int, direction: Direction, codes: Sequence[int]
    ) -> tuple[list[int], list[int]] | None:
        previous: dict[int, tuple[int, int]] = {src: (-1, -1)}  # node → (prior node, edge)
        frontier = [src]
        while frontier:
            reached: list[int] = []
            for node in frontier:
                for edge, neighbor in self._incident(node, direction, codes):
                    if neighbor in previous:
                        continue
                    previous[neighbor] = (node, edge)
                    if neighbor == dst:
                        return _unwind(previous, dst)
                    reached.append(neighbor)
            frontier = reached
        return None


def _unwind(previous: dict[int, tuple[int, int]], dst: int) -> tuple[list[int], list[int]]:
    nodes, edges = [dst], []
    node = dst
    while True:
        prior, edge = previous[node]
        if prior < 0:
            break
        nodes.append(prior)
        edges.append(edge)
        node = prior
    nodes.reverse()
    edges.reverse()
    return nodes, edges


def _codes(kinds: Iterable[EdgeKind] | None) -> list[int]:
    if kinds is None:
        return list(range(len(EDGE_KINDS)))
    return sorted({_EDGE_CODE[EdgeKind(kind)] for kind in kinds})


def _csr(count: int, endpoint: array[int], chosen: list[int]) -> tuple[array[int], array[int]]:
    """Offsets (``count + 1``) and edge positions grouped by ``endpoint`` (stable order)."""
    offsets = array("i", [0]) * (count + 1)
    for edge in chosen:
        offsets[endpoint[edge] + 1] += 1
    for position in range(count):
        offsets[position + 1] += offsets[position]
    cursor = array("i", offsets[:count])
    positions = array("i", [0]) * len(chosen)
    for edge in chosen:
        node = endpoint[edge]
        positions[cursor[node]] = edge
        cursor[node] += 1
    return offsets, positions


class SnapshotGraphCache:
    """Least-recently-used ``SnapshotGraph``s keyed by snapshot ID, evicted once their estimated
    size passes ``max_mb``. The newest entry always stays, however large. Thread-safe: each
    snapshot is loaded once even when several threads ask for it together."""

    def __init__(self, max_mb: float = DEFAULT_GRAPH_CACHE_MB) -> None:
        if max_mb <= 0:
            raise ValueError("The graph cache needs a positive size.")
        self.max_bytes = int(max_mb * 1024 * 1024)
        self._entries: OrderedDict[int, SnapshotGraph] = OrderedDict()
        self._lock = threading.Lock()
        self._loading: dict[int, threading.Lock] = {}
        self.hits = self.misses = self.evictions = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> SnapshotGraphCache:
        return cls(settings.graph_cache_mb)

    def get(self, snapshot_id: int, load: Callable[[], SnapshotGraph]) -> SnapshotGraph:
        """The cached graph of ``snapshot_id``, calling ``load()`` on a miss."""
        with self._lock:
            graph = self._touch(snapshot_id)
            if graph is not None:
                self.hits += 1
                return graph
            self.misses += 1
            gate = self._loading.setdefault(snapshot_id, threading.Lock())
        with gate:
            with self._lock:
                graph = self._touch(snapshot_id)
            if graph is not None:
                return graph
            try:
                graph = load()
            finally:
                with self._lock:
                    self._loading.pop(snapshot_id, None)
            if graph.snapshot_id != snapshot_id:
                raise ValueError(f"Loaded snapshot {graph.snapshot_id}, not {snapshot_id}.")
            with self._lock:
                self._entries[snapshot_id] = graph
                self._shrink()
            return graph

    def peek(self, snapshot_id: int) -> SnapshotGraph | None:
        """The cached graph, without loading it or changing its recency."""
        with self._lock:
            return self._entries.get(snapshot_id)

    def discard(self, snapshot_id: int) -> None:
        with self._lock:
            self._entries.pop(snapshot_id, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def snapshot_ids(self) -> list[int]:
        """Cached snapshot IDs, least recently used first."""
        with self._lock:
            return list(self._entries)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return sum(graph.estimated_bytes for graph in self._entries.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, snapshot_id: object) -> bool:
        with self._lock:
            return snapshot_id in self._entries

    def shrink(self) -> None:
        """Evict until under the cap (after memoized results grew an entry)."""
        with self._lock:
            self._shrink()

    def _touch(self, snapshot_id: int) -> SnapshotGraph | None:
        graph = self._entries.get(snapshot_id)
        if graph is not None:
            self._entries.move_to_end(snapshot_id)
        return graph

    def _shrink(self) -> None:
        total = sum(graph.estimated_bytes for graph in self._entries.values())
        while total > self.max_bytes and len(self._entries) > 1:
            _, evicted = self._entries.popitem(last=False)
            total -= evicted.estimated_bytes
            self.evictions += 1
