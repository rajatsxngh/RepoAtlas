"""``build_graph`` (GRA-1): link parse results into one deterministic code graph.

Pure: no I/O, clock, or randomness, so the same inputs always give the same digest. The stages,
in order: structure (``dir``/``file``/symbol nodes), imports, class hierarchies and ``inherits``,
routes (with inline handlers), calls, test links, public markers, owners, ``contains``.

``link_graph`` is the same build with two additions for incremental rebuilds (PRF-2, decision
0024): it returns a record per file, and, given the previous build, it reuses each file's call
resolution unless an invalidation rule says the file's calls may have changed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from repoatlas.config import Settings
from repoatlas.graph.bindings import BindingTable, ModuleResolvers, ResolvedStmt, add_import_edges
from repoatlas.graph.calls import CallResolver, add_file_call_edges
from repoatlas.graph.classes import ClassHierarchy, add_inherits_edges, with_unresolved_bases
from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.incremental import (
    ALL_RULES,
    STALE,
    FileCalls,
    FileRecord,
    Previous,
    References,
    ReusePlan,
    Rule,
    imports_digest,
    plan_reuse,
    references,
)
from repoatlas.graph.index import SymbolIndex
from repoatlas.graph.keys import file_key
from repoatlas.graph.model import CodeGraph, EdgeKind, GraphStats, UnresolvedCall
from repoatlas.graph.names import NameResolver
from repoatlas.graph.owners import DEFAULT_OWNER_COMMITS, CommitLog, compute_owners
from repoatlas.graph.project import ProjectFiles
from repoatlas.graph.public import mark_public, public_symbols
from repoatlas.graph.routes import add_routes, range_key
from repoatlas.graph.structure import add_contains_edges, build_structure
from repoatlas.graph.testlinks import add_tested_by_edges
from repoatlas.graph.tiers import TIERS_VERSION, Tier
from repoatlas.ingest.inventory import Inventory
from repoatlas.parsing.models import CallSite, ParsedFile

GRAPH_VERSION = 1  # bump when build_graph's output changes for the same input


class GraphSettings(BaseModel):
    """The settings that change a graph (part of a snapshot's configuration)."""

    model_config = ConfigDict(frozen=True)

    owner_commits: int = Field(default=DEFAULT_OWNER_COMMITS, ge=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> GraphSettings:
        return cls(owner_commits=settings.owner_commits)


@dataclass(frozen=True)
class LinkResult:
    """A linked graph, what the build keeps per file for the next one, and (when a previous
    build was given) which files' calls were resolved again and why."""

    graph: CodeGraph
    records: dict[str, FileRecord]
    plan: ReusePlan | None = None

    def stats(self) -> dict[str, JsonValue]:
        """How much call resolution was reused, for the snapshot's stats."""
        if self.plan is None:
            return {"files_resolved": len(self.records), "files_reused": 0, "by_rule": {}}
        by_rule: dict[str, JsonValue] = dict(self.plan.by_rule())
        return {
            "files_resolved": len(self.plan.resolve),
            "files_reused": len(self.plan.reuse),
            "by_rule": by_rule,
        }


def build_graph(
    inventory: Inventory,
    parsed_files: Sequence[ParsedFile],
    history: CommitLog | None = None,
    settings: GraphSettings | None = None,
    *,
    project: ProjectFiles | None = None,
) -> CodeGraph:
    """Build the graph of one snapshot.

    ``history`` is the commit log for owners (``graph.owners.read_commit_log``); ``project`` holds
    the configuration files resolution reads (``pyproject.toml``, ``tsconfig.json``,
    ``package.json``; see ``pipeline.graph.read_project_files``).
    """
    return link_graph(inventory, parsed_files, history, settings, project=project).graph


def link_graph(
    inventory: Inventory,
    parsed_files: Sequence[ParsedFile],
    history: CommitLog | None = None,
    settings: GraphSettings | None = None,
    *,
    project: ProjectFiles | None = None,
    previous: Previous | None = None,
    rules: Collection[Rule] = ALL_RULES,
) -> LinkResult:
    """``build_graph``, also returning each file's record for the next build.

    With ``previous`` (the previous build of the same repository under the same configuration),
    call resolution is reused for every file no invalidation rule touches
    (``graph.incremental``); every other pass runs in full. The graph equals what a build
    without ``previous`` gives.
    """
    settings = settings or GraphSettings()
    project = project or ProjectFiles()
    files = sorted(parsed_files, key=lambda parsed: parsed.path)
    nodes = build_structure(inventory, files)
    edges = EdgeSet()

    resolvers = ModuleResolvers.for_snapshot(inventory, files, project)
    bindings: dict[str, BindingTable] = {}
    resolved: list[tuple[str, list[ResolvedStmt]]] = []
    digests: dict[str, str] = {}
    refs: dict[str, References] = {}
    for parsed in files:
        table, statements = BindingTable.build(parsed, resolvers)
        bindings[parsed.path] = table
        resolved.append((parsed.path, statements))
        digests[parsed.path] = imports_digest(statements, table)
        if previous is not None:
            refs[parsed.path] = references(statements, table)
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

    plan: ReusePlan | None = None
    kept: Mapping[str, FileCalls] = {}
    if previous is not None:
        plan = plan_reuse(files, digests, refs, previous, rules=rules)
        kept = previous.load_calls(plan.reuse)
        plan = _without_stale(plan, kept, nodes)

    resolver = CallResolver(names, hierarchy)
    unresolved: list[UnresolvedCall] = []
    call_counts: Counter[str] = Counter()
    records: dict[str, FileRecord] = {}
    for parsed in files:
        if plan is not None and previous is not None and parsed.path not in plan.resolve:
            stored = kept.get(parsed.path, _NO_CALLS)
            for edge in stored.edges:
                edges.restore(edge, counted=True)
            file_unresolved = list(stored.unresolved)
            file_counts = Counter(previous.records[parsed.path].calls)
        else:
            file_unresolved, file_counts = add_file_call_edges(parsed, resolver, edges, caller_of)
        unresolved.extend(file_unresolved)
        call_counts.update(file_counts)
        records[parsed.path] = FileRecord(
            imports_digest=digests[parsed.path], calls=dict(sorted(file_counts.items()))
        )

    add_tested_by_edges(inventory, resolved, edges.edges(EdgeKind.CALLS), nodes, edges)
    test_paths = [entry.path for entry in inventory.files if entry.is_test]
    public = public_symbols(files, resolvers.python, resolvers.typescript, project, test_paths)
    mark_public(nodes, public)

    log = history or CommitLog()
    owners = compute_owners(log, (entry.path for entry in inventory.files), settings.owner_commits)

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
        history_commits=min(len(log.commits), settings.owner_commits),
        files_with_owners=len(owners),
    )
    graph = CodeGraph(
        nodes=nodes, edges=edge_list, unresolved=unresolved, owners=owners, stats=stats
    )
    return LinkResult(graph=graph, records=records, plan=plan)


_NO_CALLS = FileCalls()


def _without_stale(
    plan: ReusePlan, kept: Mapping[str, FileCalls], nodes: Mapping[str, object]
) -> ReusePlan:
    """Move a file whose stored calls name a node that no longer exists back to ``resolve``.
    The rules should never let that happen; if it does, the file is resolved rather than
    given an edge to nowhere."""
    stale = []
    for path in plan.reuse:
        stored = kept.get(path, _NO_CALLS)
        if any(edge.src not in nodes or edge.dst not in nodes for edge in stored.edges) or any(
            call.caller_key not in nodes for call in stored.unresolved
        ):
            stale.append(path)
    if not stale:
        return plan
    gone = set(stale)
    return ReusePlan(
        resolve={**plan.resolve, **dict.fromkeys(stale, STALE)},
        reuse=[path for path in plan.reuse if path not in gone],
    )


def component_versions() -> dict[str, int]:
    """Versions of the graph builder and tier table, for configuration fingerprints."""
    return {"graph": GRAPH_VERSION, "tiers": TIERS_VERSION}


def _count(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
