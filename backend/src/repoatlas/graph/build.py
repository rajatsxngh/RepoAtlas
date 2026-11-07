"""``build_graph`` (GRA-1): link parse results into one deterministic code graph.

Pure: no I/O, clock, or randomness, so the same inputs always give the same digest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from repoatlas.graph.bindings import BindingTable, ModuleResolvers, ResolvedStmt, add_import_edges
from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.model import CodeGraph, GraphStats
from repoatlas.graph.project import ProjectFiles
from repoatlas.graph.structure import add_contains_edges, build_structure
from repoatlas.ingest.inventory import Inventory
from repoatlas.parsing.models import ParsedFile


def build_graph(
    inventory: Inventory,
    parsed_files: Sequence[ParsedFile],
    *,
    project: ProjectFiles | None = None,
) -> CodeGraph:
    """Build the graph of one snapshot from its inventory, parse results, and config files."""
    files = sorted(parsed_files, key=lambda parsed: parsed.path)
    nodes = build_structure(inventory, files)
    edges = EdgeSet()

    resolvers = ModuleResolvers.for_snapshot(inventory, files, project)
    bindings: dict[str, BindingTable] = {}
    resolved: list[tuple[str, list[ResolvedStmt]]] = []
    for parsed in files:
        bindings[parsed.path], statements = BindingTable.build(parsed, resolvers)
        resolved.append((parsed.path, statements))
    import_outcomes = add_import_edges(resolved, edges)

    add_contains_edges(nodes.values(), edges)
    edge_list = edges.edges()
    stats = GraphStats(
        nodes_by_kind=_count(node.kind.value for node in nodes.values()),
        edges_by_kind=_count(edge.kind.value for edge in edge_list),
        import_statements=sum(import_outcomes.values()),
        imports_by_outcome=dict(sorted(import_outcomes.items())),
    )
    return CodeGraph(nodes=nodes, edges=edge_list, stats=stats)


def _count(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
