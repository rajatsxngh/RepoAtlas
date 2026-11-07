"""Bindings and import edges (GRA-3, GRA-4, GRA-6).

A ``BindingTable`` maps the names one file binds through imports to their targets (a module, a
symbol, a non-symbol member, or something external), per scope. ``imports`` edges link a file to
every repository file it imports, one edge per pair of files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.keys import file_key
from repoatlas.graph.model import EdgeKind, Provenance
from repoatlas.graph.project import ProjectFiles, python_roots, ts_configs
from repoatlas.graph.python_resolver import PythonModuleResolver
from repoatlas.graph.targets import ModuleRef, ResolvedImport, Target
from repoatlas.graph.tiers import IMPORT_CONFIDENCE
from repoatlas.graph.ts_resolver import TsModuleResolver
from repoatlas.ingest.inventory import Inventory
from repoatlas.parsing.models import ImportStmt, ParsedFile

ResolvedStmt = tuple[ImportStmt, ResolvedImport]


class ModuleResolvers:
    """The Python and TypeScript/JavaScript resolvers of one snapshot, picked by file type."""

    def __init__(self, python: PythonModuleResolver, typescript: TsModuleResolver) -> None:
        self.python = python
        self.typescript = typescript

    @classmethod
    def for_snapshot(
        cls,
        inventory: Inventory,
        parsed_files: Sequence[ParsedFile],
        project: ProjectFiles | None = None,
    ) -> ModuleResolvers:
        project = project or ProjectFiles()
        paths = [entry.path for entry in inventory.files]
        return cls(
            PythonModuleResolver(parsed_files, paths, roots=python_roots(project, paths)),
            TsModuleResolver(parsed_files, paths, configs=ts_configs(project)),
        )

    def resolve_import(self, parsed: ParsedFile, stmt: ImportStmt) -> ResolvedImport:
        if parsed.language == "py":
            return self.python.resolve_import(parsed.path, stmt)
        return self.typescript.resolve_import(parsed.path, stmt)

    def member(self, module: ModuleRef, name: str) -> Target:
        """What ``module.name`` refers to (a Python attribute or a TS/JS export)."""
        if module.file is None or module.file.endswith(".py"):
            return self.python.lookup(module, name)
        return self.typescript.lookup_export(module.file, name)

    def called_module(self, module: ModuleRef) -> Target | None:
        """What calling a module object reaches: a CommonJS module's ``module.exports``."""
        if module.file is None or module.file.endswith(".py"):
            return None
        return self.typescript.lookup_export(module.file, "default")


@dataclass
class BindingTable:
    """Local names bound by one file's imports.

    ``module`` holds module-level bindings, ``scoped`` the bindings of imports inside a function
    or class (keyed by that symbol's key), and ``stars`` the names a Python ``from m import *``
    binds (looked up after the explicit module-level bindings). Later imports of the same name
    in a scope win, as at runtime.
    """

    path: str
    module: dict[str, Target] = field(default_factory=dict)
    scoped: dict[str, dict[str, Target]] = field(default_factory=dict)
    stars: dict[str, Target] = field(default_factory=dict)

    def lookup(self, name: str, scopes: Iterable[str | None] = (None,)) -> Target | None:
        """The binding of ``name`` in the first scope that has one (``None`` = module scope)."""
        for scope in scopes:
            if scope is None:
                if name in self.module:
                    return self.module[name]
                if name in self.stars:
                    return self.stars[name]
            else:
                table = self.scoped.get(scope)
                if table is not None and name in table:
                    return table[name]
        return None

    @classmethod
    def build(
        cls, parsed: ParsedFile, resolvers: ModuleResolvers
    ) -> tuple[BindingTable, list[ResolvedStmt]]:
        """The file's bindings, plus how each of its import statements resolved."""
        table = cls(parsed.path)
        resolved: list[ResolvedStmt] = []
        for stmt in parsed.imports:
            result = resolvers.resolve_import(parsed, stmt)
            resolved.append((stmt, result))
            names = table.module if stmt.scope is None else table.scoped.setdefault(stmt.scope, {})
            for local, target in result.bindings:
                names[local] = target
            if stmt.scope is None:
                for star in result.stars:
                    for name in sorted(resolvers.python.public_names(star)):
                        table.stars[name] = resolvers.python.lookup(star, name)
        return table, resolved


def add_import_edges(
    resolved_files: Iterable[tuple[str, list[ResolvedStmt]]], edges: EdgeSet
) -> Counter[str]:
    """``imports`` edges file → file and the outcome count of every import statement.

    One edge per pair of files: ``site`` is the first statement's range, ``meta.count`` the
    number of statements, and ``meta.type_only``/``meta.dynamic`` are set when every one of them
    is type-only or a dynamic ``import()``.
    """
    outcomes: Counter[str] = Counter()
    pairs: dict[tuple[str, str], list[ImportStmt]] = {}
    for path, statements in resolved_files:
        for stmt, result in statements:
            outcomes[result.outcome] += 1
            for target in result.files:
                pairs.setdefault((path, target), []).append(stmt)
    for (src, dst), stmts in pairs.items():
        first = min(stmts, key=lambda s: (s.range.start_line, s.range.start_col))
        meta: dict[str, JsonValue] = {"count": len(stmts)}
        if all(s.type_only for s in stmts):
            meta["type_only"] = True
        if all(s.kind == "dynamic" for s in stmts):
            meta["dynamic"] = True
        edges.add(
            file_key(src),
            file_key(dst),
            EdgeKind.IMPORTS,
            Provenance.IMPORT_RESOLVER,
            IMPORT_CONFIDENCE,
            site=first.range,
            meta=meta,
        )
    return outcomes
