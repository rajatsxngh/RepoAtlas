"""Which files' calls must be resolved again, and what a build keeps for the next one (PRF-2,
decision 0024).

Linking a graph runs cheap whole-repository passes (structure, imports, hierarchies, routes,
test links) in full. Call resolution does work per call site, so its result is kept per file:
the next build of the same repository reuses a file's ``calls`` edges, unresolved calls, and
call-site counts unless one of these rules invalidates the file.

- ``changed``: the file is modified or added (or the previous build has no record of it).
- ``imports``: its import statements resolve differently now. Checked exactly: the digest of
  the file's resolved imports and star bindings is compared with the one the previous build
  recorded. An added file that an unresolved import now reaches, a deleted or renamed target, a
  file that shadows another, and changed project configuration all show here.
- ``dependency``: a file it can reach through imports and bindings, at any depth, is modified,
  added, or resolves its imports differently; or a path was added to or removed from a package
  it can reach (Python finds ``pkg.sub`` by path). This is what catches a method removed from a
  base class two imports away.
- ``name_match``: one of its call sites has the name of a symbol defined in a file of the kinds
  above, or in the previous version of a modified or deleted file. Calls on unknown receivers
  are matched by member name across the whole repository, so such a call can change without
  any import between the two files.

Why these are enough: a file's call resolution reads its own parse result, its bindings, and
through them the symbols, exports, and class hierarchies of the files it can reach; and, for
name-matched calls, every definer of the call's name with that definer's hierarchy. If no rule
fires, each of those inputs is the same as in the previous build.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from repoatlas.canonical import digest
from repoatlas.graph.bindings import BindingTable, ResolvedStmt
from repoatlas.graph.keys import ROOT_DIR, ancestors
from repoatlas.graph.model import CodeGraph, Edge, EdgeKind, NodeKind, UnresolvedCall
from repoatlas.graph.targets import Target
from repoatlas.parsing.models import ParsedFile

Rule = Literal["changed", "imports", "dependency", "name_match"]
ALL_RULES: frozenset[Rule] = frozenset({"changed", "imports", "dependency", "name_match"})
# A reused edge whose target is gone: no rule should allow it, so the file is resolved again.
STALE = "stale_target"


class FileRecord(BaseModel):
    """What a build keeps about one parsed file for the next build."""

    model_config = ConfigDict(frozen=True)

    imports_digest: str
    calls: dict[str, int] = {}  # call-site counts: T1…T6, unresolved:<reason>, external, builtin


@dataclass(frozen=True)
class FileCalls:
    """One file's call-resolution result, as the previous graph stored it."""

    edges: list[Edge] = field(default_factory=list)
    unresolved: list[UnresolvedCall] = field(default_factory=list)


@dataclass(frozen=True)
class Previous:
    """What linking needs from the previous build of the repository.

    ``arrived`` and ``gone`` are the change set's paths (``pipeline.changes.ChangeSet``);
    ``old_names`` the symbol names the previous snapshot defined in the files that are now
    modified or gone; ``load_calls(paths)`` reads the stored call results of those files."""

    records: Mapping[str, FileRecord]
    arrived: Collection[str]
    gone: Collection[str]
    old_names: Collection[str]
    load_calls: Callable[[Collection[str]], Mapping[str, FileCalls]]


@dataclass(frozen=True)
class ReusePlan:
    """``resolve``: files whose calls are resolved, with the first rule that says so (in the
    order changed, imports, dependency, name_match). ``reuse``: files whose stored result is
    kept."""

    resolve: dict[str, str]
    reuse: list[str]

    def by_rule(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rule in self.resolve.values():
            counts[rule] = counts.get(rule, 0) + 1
        return dict(sorted(counts.items()))


def _target(target: Target) -> list[str | None]:
    module = target.module
    return [
        target.kind,
        target.path,
        target.key,
        target.name,
        None if module is None else module.file,
        None if module is None else module.package,
    ]


def imports_digest(statements: Sequence[ResolvedStmt], table: BindingTable) -> str:
    """A digest of how one file's imports resolved: every statement's outcome, files, bindings,
    and star modules, in order, and the names its star imports bind."""
    return digest(
        {
            "statements": [
                [
                    result.outcome,
                    list(result.files),
                    [[local, _target(target)] for local, target in result.bindings],
                    [[star.file, star.package] for star in result.stars],
                ]
                for _stmt, result in statements
            ],
            "stars": {name: _target(target) for name, target in table.stars.items()},
        }
    )


@dataclass(frozen=True)
class References:
    """What one file's resolution can reach directly: files, and packages (whose modules are
    found by path)."""

    files: frozenset[str]
    packages: frozenset[str]


def references(statements: Sequence[ResolvedStmt], table: BindingTable) -> References:
    files: set[str] = set()
    packages: set[str] = set()

    def reach(target: Target) -> None:
        if target.path is not None:
            files.add(target.path)
        if target.module is not None:
            if target.module.file is not None:
                files.add(target.module.file)
            if target.module.package is not None:
                packages.add(target.module.package)

    for _stmt, result in statements:
        files.update(result.files)
        for _local, target in result.bindings:
            reach(target)
        for star in result.stars:
            if star.file is not None:
                files.add(star.file)
            if star.package is not None:
                packages.add(star.package)
    for target in table.stars.values():
        reach(target)
    return References(frozenset(files), frozenset(packages))


def _folders(path: str) -> Iterable[str]:
    """The directories holding ``path``, without the root."""
    return (folder for folder in ancestors(path) if folder != ROOT_DIR)


def tainted_files(dirty: Iterable[str], refs: Mapping[str, References]) -> set[str]:
    """``dirty`` and every file that can reach a dirty path, at any depth: through a file it
    references, or through a package it references that holds the path."""
    users_of_file: dict[str, list[str]] = {}
    users_of_package: dict[str, list[str]] = {}
    for path, found in refs.items():
        for target in found.files:
            users_of_file.setdefault(target, []).append(path)
        for package in found.packages:
            users_of_package.setdefault(package, []).append(path)
    seen: set[str] = set()
    queue: deque[str] = deque(dirty)
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        queue.extend(users_of_file.get(path, ()))
        for folder in _folders(path):
            queue.extend(users_of_package.get(folder, ()))
    return seen


def plan_reuse(
    files: Sequence[ParsedFile],
    digests: Mapping[str, str],
    refs: Mapping[str, References],
    previous: Previous,
    *,
    rules: Collection[Rule] = ALL_RULES,
) -> ReusePlan:
    """Sort ``files`` into those resolved again and those whose stored calls are kept.

    ``digests`` and ``refs`` are this build's import digests and references per file.
    ``rules`` exists for tests, which switch one rule off to show the graph is wrong without it
    (``changed`` can't be switched off).
    """
    arrived = set(previous.arrived)
    resolve: dict[str, str] = {}
    for parsed in files:
        if parsed.path in arrived or parsed.path not in previous.records:
            resolve[parsed.path] = "changed"

    differ: set[str] = set()
    if "imports" in rules:
        differ = {
            parsed.path
            for parsed in files
            if parsed.path in previous.records
            and previous.records[parsed.path].imports_digest != digests[parsed.path]
        }
        for path in sorted(differ):
            resolve.setdefault(path, "imports")

    tainted = tainted_files(arrived | set(previous.gone) | differ, refs)
    if "dependency" in rules:
        for parsed in files:
            if parsed.path in tainted:
                resolve.setdefault(parsed.path, "dependency")

    if "name_match" in rules:
        names = set(previous.old_names)
        for parsed in files:
            if parsed.path in tainted:
                names.update(symbol.name for symbol in parsed.symbols)
        for parsed in files:
            if parsed.path not in resolve and any(call.name in names for call in parsed.calls):
                resolve[parsed.path] = "name_match"

    reuse = [parsed.path for parsed in files if parsed.path not in resolve]
    return ReusePlan(resolve=resolve, reuse=reuse)


def calls_by_file(graph: CodeGraph) -> dict[str, FileCalls]:
    """Each file's call-resolution result in ``graph``: the ``calls`` edges and unresolved calls
    whose caller is a node of the file."""
    found: dict[str, FileCalls] = {}
    for edge in graph.edges:
        if edge.kind == EdgeKind.CALLS:
            found.setdefault(graph.nodes[edge.src].path, FileCalls()).edges.append(edge)
    for call in graph.unresolved:
        found.setdefault(graph.nodes[call.caller_key].path, FileCalls()).unresolved.append(call)
    return found


def previous_from_graph(
    graph: CodeGraph,
    records: Mapping[str, FileRecord],
    *,
    arrived: Collection[str],
    gone: Collection[str],
    modified: Collection[str],
) -> Previous:
    """A ``Previous`` over a graph held in memory (the store reads the same from its tables)."""
    by_file = calls_by_file(graph)
    replaced = set(gone) | set(modified)
    old_names = {
        node.name
        for node in graph.nodes.values()
        if node.path in replaced and node.kind not in (NodeKind.DIR, NodeKind.FILE)
    }
    return Previous(
        records=records,
        arrived=arrived,
        gone=gone,
        old_names=old_names,
        load_calls=lambda paths: {path: by_file[path] for path in paths if path in by_file},
    )
