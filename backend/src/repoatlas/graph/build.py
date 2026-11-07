"""``build_graph`` (GRA-1): link parse results into one deterministic code graph.

Pure: no I/O, clock, or randomness, so the same inputs always give the same digest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.model import CodeGraph, GraphStats
from repoatlas.graph.structure import add_contains_edges, build_structure
from repoatlas.ingest.inventory import Inventory
from repoatlas.parsing.models import ParsedFile


def build_graph(inventory: Inventory, parsed_files: Sequence[ParsedFile]) -> CodeGraph:
    """Build the graph of one snapshot from its inventory and parse results."""
    nodes = build_structure(inventory, parsed_files)
    edges = EdgeSet()
    add_contains_edges(nodes.values(), edges)
    edge_list = edges.edges()
    stats = GraphStats(
        nodes_by_kind=_count(node.kind.value for node in nodes.values()),
        edges_by_kind=_count(edge.kind.value for edge in edge_list),
    )
    return CodeGraph(nodes=nodes, edges=edge_list, stats=stats)


def _count(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
