"""Synthetic graphs for impact tests."""

from __future__ import annotations

from collections.abc import Iterable

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.store.cache import EdgeRecord, NodeRecord, SnapshotGraph

CALLS = EdgeKind.CALLS
EdgeSpec = tuple[str, str] | tuple[str, str, EdgeKind] | tuple[str, str, EdgeKind, float]


def fn(name: str, kind: str = "function", path: str = "m.py") -> str:
    """A symbol key for ``name`` (``C.m`` style qualified names for methods)."""
    return f"py:{path}:{name}:{kind}"


def synthetic(
    edges: Iterable[EdgeSpec],
    *,
    kinds: dict[str, NodeKind] | None = None,
    parents: dict[str, str] | None = None,
    nodes: Iterable[str] = (),
    confidence: float = 1.0,
) -> SnapshotGraph:
    """A graph whose nodes are named by ``edges`` (``calls`` with ``confidence`` by default).
    Node kinds default to ``function``; ``parents`` sets containment."""
    specs: list[tuple[str, str, EdgeKind, float]] = []
    for edge in edges:
        src, dst = edge[0], edge[1]
        kind = edge[2] if len(edge) >= 3 else CALLS  # type: ignore[misc]
        conf = edge[3] if len(edge) == 4 else confidence  # type: ignore[misc]
        specs.append((src, dst, kind, conf))
    parents = parents or {}
    names = sorted(
        {*nodes, *parents, *parents.values(), *(s[0] for s in specs), *(s[1] for s in specs)}
    )
    ids = {name: 10 + position for position, name in enumerate(names)}
    kinds = kinds or {}
    records = [
        NodeRecord(
            ids[name],
            name,
            kinds.get(name, NodeKind.FUNCTION),
            ids[parents[name]] if name in parents else None,
        )
        for name in names
    ]
    edge_records = [
        EdgeRecord(1_000 + i, ids[src], ids[dst], kind, conf)
        for i, (src, dst, kind, conf) in enumerate(specs)
    ]
    for name, parent in parents.items():
        edge_records.append(
            EdgeRecord(5_000 + len(edge_records), ids[parent], ids[name], EdgeKind.CONTAINS, 1.0)
        )
    return SnapshotGraph(1, records, edge_records)
