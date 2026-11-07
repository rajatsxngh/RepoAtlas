"""``build_graph`` (GRA-1): link parse results into one deterministic code graph.

Pure: no I/O, clock, or randomness, so the same inputs always give the same digest.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from repoatlas.graph.bindings import BindingTable, ModuleResolvers, ResolvedStmt, add_import_edges
from repoatlas.graph.calls import CallResolver, add_call_edges
from repoatlas.graph.classes import ClassHierarchy, add_inherits_edges, with_unresolved_bases
from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.index import SymbolIndex
from repoatlas.graph.keys import file_key
from repoatlas.graph.model import CodeGraph, EdgeKind, GraphStats
from repoatlas.graph.names import NameResolver
from repoatlas.graph.project import ProjectFiles
from repoatlas.graph.public import mark_public, public_symbols
from repoatlas.graph.routes import add_routes, range_key
from repoatlas.graph.structure import add_contains_edges, build_structure
from repoatlas.graph.testlinks import add_tested_by_edges
from repoatlas.graph.tiers import Tier
from repoatlas.ingest.inventory import Inventory
from repoatlas.parsing.models import CallSite, ParsedFile


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

    index = SymbolIndex(files, test_paths=(e.path for e in inventory.files if e.is_test))
    names = NameResolver(index, bindings, resolvers)
    hierarchy = ClassHierarchy.build(files, names)
    names.hierarchy = hierarchy
    add_inherits_edges(hierarchy, edges)
    with_unresolved_bases(nodes, hierarchy)

    routes = add_routes(files, names, resolvers.typescript, nodes, edges)
    nodes.update(routes.nodes)

    def caller_of(parsed: ParsedFile, call: CallSite) -> str:
        inline = routes.callers.get((parsed.path, range_key(call.range)))
        if inline is not None:
            return inline
        return call.enclosing if call.enclosing in nodes else file_key(parsed.path)

    unresolved, call_counts = add_call_edges(
        files, CallResolver(names, hierarchy), edges, caller_of
    )
    add_tested_by_edges(inventory, resolved, edges.edges(EdgeKind.CALLS), nodes, edges)
    mark_public(
        nodes,
        public_symbols(files, resolvers.python, resolvers.typescript, project or ProjectFiles()),
    )

    add_contains_edges(nodes.values(), edges)
    edge_list = edges.edges()
    tiers = {tier.value: call_counts[tier.value] for tier in Tier if call_counts[tier.value]}
    reasons = {
        key.removeprefix("unresolved:"): count
        for key, count in sorted(call_counts.items())
        if key.startswith("unresolved:")
    }
    stats = GraphStats(
        nodes_by_kind=_count(node.kind.value for node in nodes.values()),
        edges_by_kind=_count(edge.kind.value for edge in edge_list),
        call_sites=sum(call_counts.values()),
        call_sites_by_tier=tiers,
        unresolved_calls=sum(reasons.values()),
        unresolved_by_reason=reasons,
        external_calls=call_counts["external"],
        builtin_calls=call_counts["builtin"],
        import_statements=sum(import_outcomes.values()),
        imports_by_outcome=dict(sorted(import_outcomes.items())),
    )
    return CodeGraph(nodes=nodes, edges=edge_list, unresolved=unresolved, stats=stats)


def _count(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
