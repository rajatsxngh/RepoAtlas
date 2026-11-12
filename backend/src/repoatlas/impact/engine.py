"""The impact engine (IMP-1 to IMP-5): what breaks if a symbol, or what a diff touches, changes.

``analyze`` resolves the target to changed symbols, spreads impact backwards through the graph
(``repoatlas.impact.traversal``), keeps the items at or above ``min_score`` in one combined
ranking (``repoatlas.impact.groups``), attaches each item's best path and evidence, and lists the
unknown sites (``repoatlas.impact.unknown``).

A changed top-level symbol that its module exports (``exported`` or ``meta.public``), and a
change to module-level code (the file itself), also reach the files importing that file and the
tests linked to it: the file joins the search as a start that follows only ``imports`` and
``tested_by``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from repoatlas.graph.model import Edge, EdgeKind, Node, NodeKind
from repoatlas.impact.data import ImpactData
from repoatlas.impact.groups import count_by_group, group_of, rank_key
from repoatlas.impact.schema import (
    DiffTarget,
    ImpactItem,
    ImpactParams,
    ImpactReport,
    ImpactStats,
    PathStep,
    SymbolTarget,
    UnknownSite,
)
from repoatlas.impact.traversal import Propagation, Reach, Step, propagate
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.store.cache import SnapshotGraph
from repoatlas.store.errors import InvalidQuery, NodeNotFound

MODULE_KINDS = (EdgeKind.IMPORTS, EdgeKind.TESTED_BY)


class ImpactEngine:
    def __init__(
        self,
        data: ImpactData,
        *,
        params: ImpactParams | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.data = data
        self.params = params or ImpactParams()
        self.clock = clock

    def analyze(
        self,
        snapshot_id: int,
        target: SymbolTarget | DiffTarget,
        params: ImpactParams | None = None,
    ) -> ImpactReport:
        params = params or self.params
        started = self.clock()
        graph = self.data.graph(snapshot_id)
        keys = self._targets(snapshot_id, graph, target)
        target_nodes = self.data.nodes(snapshot_id, keys)

        starts: dict[int, Sequence[EdgeKind] | None] = {graph.index[key]: None for key in keys}
        entered_by: dict[int, str] = {}  # a module start → the changed symbol it stands for
        for key in keys:
            node = target_nodes.get(key)
            if node is None or not _module_level_export(node):
                continue
            file = _file_of(graph, graph.index[key])
            if file is not None and file not in starts:
                starts[file] = MODULE_KINDS
                entered_by[file] = key

        propagation = propagate(
            graph,
            starts,
            max_depth=params.max_depth,
            decay=params.decay,
            min_score=params.min_score,
            edge_kinds=params.edge_kinds,
        )
        reached = propagation.items()
        above = sorted(
            (r for r in reached if r.score >= params.min_score),
            key=lambda r: rank_key(_Ranked(graph.keys[r.node], r)),
        )
        kept = above[: params.limit]
        items = self._items(snapshot_id, graph, propagation, kept, entered_by)
        unknown = self._unknown(snapshot_id, graph, keys, target_nodes)
        stats = ImpactStats(
            targets=len(keys),
            reached=len(reached),
            expanded=propagation.expanded,
            items=len(above),
            returned=len(items),
            truncated=len(above) > len(kept),
            by_group=count_by_group(item.group for item in items),
            unknown=len(unknown),
            elapsed_ms=round((self.clock() - started) * 1000, 3),
        )
        return ImpactReport(
            target=target,
            targets=keys,
            params=params,
            items=items,
            unknown=unknown,
            stats=stats,
        )

    # --- targets ---------------------------------------------------------------------------

    def _targets(
        self, snapshot_id: int, graph: SnapshotGraph, target: SymbolTarget | DiffTarget
    ) -> list[str]:
        if isinstance(target, SymbolTarget):
            if target.symbol_key not in graph.index:
                raise NodeNotFound(f"Snapshot {snapshot_id} has no node {target.symbol_key!r}.")
            return [target.symbol_key]
        raise InvalidQuery("Diff targets are not supported yet.")

    # --- items -----------------------------------------------------------------------------

    def _items(
        self,
        snapshot_id: int,
        graph: SnapshotGraph,
        propagation: Propagation,
        kept: list[Reach],
        entered_by: dict[int, str],
    ) -> list[ImpactItem]:
        if not kept:
            return []
        keys = [graph.keys[r.node] for r in kept]
        nodes = self.data.nodes(snapshot_id, keys)
        positions = sorted(
            {r.via.edge for r in kept if r.via is not None and r.via.edge is not None}
        )
        edges = self.data.edges(snapshot_id, positions)
        oids = self.data.blob_oids(snapshot_id, sorted({n.path for n in nodes.values()}))
        items = []
        for reach in kept:
            key = graph.keys[reach.node]
            node = nodes.get(key)
            if node is None:  # the adjacency and the details disagree: skip, never guess
                continue
            path = [_path_step(graph, step) for step in propagation.path_to(reach.node)]
            first = propagation.path_to(reach.node)[0].src if path else None
            if first is not None and first in entered_by:
                path.insert(
                    0,
                    PathStep(
                        src=entered_by[first],
                        dst=graph.keys[first],
                        kind="contains",
                        confidence=1.0,
                    ),
                )
            edge = edges.get(reach.via.edge) if reach.via and reach.via.edge is not None else None
            items.append(
                ImpactItem(
                    symbol_key=key,
                    kind=node.kind,
                    name=node.name,
                    path=node.path,
                    group=group_of(node),
                    score=round(min(reach.score, 1.0), 6),
                    best_score=round(min(reach.best, 1.0), 6),
                    depth=reach.depth,
                    best_path=path,
                    evidence=_evidence(node, edge, oids.get(node.path)),
                )
            )
        return items

    # --- unknown sites ---------------------------------------------------------------------

    def _unknown(
        self,
        snapshot_id: int,
        graph: SnapshotGraph,
        keys: list[str],
        nodes: dict[str, Node],
    ) -> list[UnknownSite]:
        return []


class _Ranked:
    __slots__ = ("best_score", "depth", "score", "symbol_key")

    def __init__(self, key: str, reach: Reach) -> None:
        self.symbol_key = key
        self.score = reach.score
        self.best_score = reach.best
        self.depth = reach.depth


def _module_level_export(node: Node) -> bool:
    if node.kind == NodeKind.FILE:
        return False  # a file start already follows every kind
    on_top = node.parent_key is not None and node.parent_key.startswith("file:")
    return on_top and (node.exported or node.meta.get("public") is True)


def _file_of(graph: SnapshotGraph, node: int) -> int | None:
    current = node
    while current >= 0:
        if graph.kind_of(current) == NodeKind.FILE:
            return current
        current = graph.parents[current]
    return None


def _path_step(graph: SnapshotGraph, step: Step) -> PathStep:
    return PathStep(
        src=graph.keys[step.src],
        dst=graph.keys[step.dst],
        kind=step.kind,  # type: ignore[arg-type]
        confidence=round(step.confidence, 6),
    )


def _evidence(node: Node, edge: Edge | None, oid: str | None) -> list[EvidenceRef]:
    """The last step's site (it lies in the item's own file), else the item's name or span."""
    if oid is None:
        return []
    where = edge.site if edge is not None and edge.site is not None else None
    where = where or node.name_range or node.span
    if where is None:
        return []
    return [
        EvidenceRef(
            blob_oid=oid,
            path=node.path,
            start_line=where.start_line,
            end_line=max(where.end_line, where.start_line),
            symbol_key=node.key,
        )
    ]
