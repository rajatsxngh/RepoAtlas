"""Python module resolution (GRA-3): imports → repository files and symbols.

- Absolute imports search the candidate roots (``graph.project.python_roots``) in order, then the
  importing file's own directory when that directory is not a regular package (scripts and
  pytest-style test folders put it on ``sys.path``).
- ``a/b/c`` is a regular package (``a/b/c/__init__.py``), else a module (``a/b/c.py``), else a
  namespace package (a directory holding Python files but no ``__init__.py``) — the order
  CPython's path finder uses.
- Relative imports resolve against the importing module's package.
- ``from pkg import name`` binds a submodule when ``pkg/name`` exists; otherwise ``name`` is looked
  up in ``pkg/__init__.py``, following re-exports (``from .x import name``, star imports) up to
  ``MAX_REEXPORT_DEPTH`` hops.
- An absolute import that matches nothing in the repository is external. One that starts like
  repository code but doesn't resolve, or a relative import that doesn't, is missing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from repoatlas.graph.keys import parent_dir
from repoatlas.graph.project import join
from repoatlas.graph.targets import ImportOutcome, ModuleRef, ResolvedImport, Target
from repoatlas.parsing.models import ImportBinding, ImportStmt, ParsedFile, Symbol

MAX_REEXPORT_DEPTH = 3

_Reexport = tuple[ImportStmt, ImportBinding]


def _folder(path: str) -> str:
    """The directory of ``path`` as a join-able prefix ('' at the top level)."""
    folder = parent_dir(path)
    return "" if folder == "." else folder


class PythonModuleResolver:
    def __init__(
        self,
        files: Iterable[ParsedFile],
        paths: Iterable[str],
        *,
        roots: Sequence[str] = ("", "src"),
    ) -> None:
        """``files``: parsed Python files; ``paths``: every inventoried path (skipped included)."""
        self.roots = list(roots)
        self._paths = {path for path in paths if path.endswith(".py")}
        self._dirs: set[str] = set()
        for path in self._paths:
            folder = _folder(path)
            while folder:
                self._dirs.add(folder)
                folder = _folder(folder)
        self._parsed: dict[str, ParsedFile] = {}
        self._top: dict[str, dict[str, Symbol]] = {}
        self._reexports: dict[str, dict[str, _Reexport]] = {}
        self._stars: dict[str, list[ImportStmt]] = {}
        self._all: dict[str, set[str] | None] = {}
        for parsed in files:
            if parsed.language != "py":
                continue
            self._parsed[parsed.path] = parsed
            top: dict[str, Symbol] = {}
            for symbol in parsed.symbols:
                if symbol.parent is None:
                    top[symbol.name] = symbol  # a later definition wins, as at runtime
            self._top[parsed.path] = top
            reexports: dict[str, _Reexport] = {}
            stars: list[ImportStmt] = []
            for stmt in parsed.imports:
                if stmt.scope is not None:
                    continue
                for binding in stmt.bindings:
                    if binding.kind == "star":
                        stars.append(stmt)
                    elif binding.local:
                        reexports[binding.local] = (stmt, binding)
            self._reexports[parsed.path] = reexports
            self._stars[parsed.path] = stars
            self._all[parsed.path] = {e.name for e in parsed.exports} if parsed.exports else None

    # -- modules ---------------------------------------------------------------------------

    def find_module(self, dotted: str, *, importer: str | None = None) -> ModuleRef | None:
        """The repository module named ``dotted`` (absolute), or ``None``."""
        if not dotted or dotted.startswith("."):
            return None
        relative = dotted.replace(".", "/")
        for root in self._search_roots(importer):
            found = self._at(join(root, relative))
            if found is not None:
                return found
        return None

    def submodule(self, module: ModuleRef, name: str) -> ModuleRef | None:
        """``module.name`` when ``module`` is a package and ``name`` is one of its modules."""
        if module.package is None or not name:
            return None
        return self._at(join(module.package, name))

    def base_module(self, importer: str, stmt: ImportStmt) -> ModuleRef | None:
        """The module a ``from … import …`` statement imports from."""
        if stmt.level == 0:
            return self.find_module(stmt.module, importer=importer)
        package = _folder(importer)
        for _ in range(stmt.level - 1):
            if not package:
                return None  # beyond the top of the repository
            package = _folder(package)
        rest = stmt.module.lstrip(".")
        if not rest and not package:
            return None
        return self._at(join(package, *rest.split(".")))

    def is_regular_package(self, folder: str) -> bool:
        return join(folder, "__init__.py") in self._paths

    def _search_roots(self, importer: str | None) -> list[str]:
        roots = list(self.roots)
        if importer is not None:
            folder = _folder(importer)
            if folder not in roots and not self.is_regular_package(folder):
                roots.append(folder)
        return roots

    def _at(self, base: str) -> ModuleRef | None:
        if not base:
            return None
        init = f"{base}/__init__.py"
        if init in self._paths:
            return ModuleRef(file=init, package=base)
        if f"{base}.py" in self._paths:
            return ModuleRef(file=f"{base}.py")
        if base in self._dirs:
            return ModuleRef(file=None, package=base)
        return None

    # -- names -----------------------------------------------------------------------------

    def lookup(self, module: ModuleRef, name: str, depth: int = 0) -> Target:
        """What ``module.name`` refers to, following up to ``MAX_REEXPORT_DEPTH`` re-exports."""
        sub = self.submodule(module, name)
        if sub is not None:
            return Target.for_module(sub)
        if module.file is None:
            return Target.for_missing(f"{module.label}{name}")
        if module.file not in self._parsed:
            return Target.for_external(module.file)  # a file the inventory skipped
        symbol = self._top[module.file].get(name)
        if symbol is not None:
            return Target.for_symbol(symbol.key, module.file)
        if depth < MAX_REEXPORT_DEPTH:
            found = self._reexports[module.file].get(name)
            if found is not None:
                stmt, binding = found
                return self.binding_target(module.file, stmt, binding, depth + 1)
            for stmt in self._stars[module.file]:
                base = self.base_module(module.file, stmt)
                if base is not None and name in self.public_names(base):
                    return self.lookup(base, name, depth + 1)
        return Target.for_member(module.file, name)

    def public_names(self, module: ModuleRef) -> set[str]:
        """Names ``from module import *`` binds: ``__all__``, else public top-level names."""
        if module.file is None or module.file not in self._parsed:
            return set()
        listed = self._all[module.file]
        if listed is not None:
            return set(listed)
        names = set(self._top[module.file]) | set(self._reexports[module.file])
        return {name for name in names if not name.startswith("_")}

    def binding_target(
        self, importer: str, stmt: ImportStmt, binding: ImportBinding, depth: int = 0
    ) -> Target:
        """The target of one binding of an import statement in ``importer``."""
        if binding.kind == "module":  # import a.b.c → a;  import a.b.c as x → a.b.c
            dotted = binding.name or stmt.module
            head = dotted.split(".", 1)[0]
            bound = head if binding.local == head else dotted
            found = self.find_module(bound, importer=importer)
            return Target.for_module(found) if found else self._absent(dotted, importer)
        if binding.kind == "named" and binding.name:
            base = self.base_module(importer, stmt)
            if base is None:
                return self._absent_from(importer, stmt)
            return self.lookup(base, binding.name, depth)
        return Target.for_missing(stmt.module)

    def resolve_import(self, importer: str, stmt: ImportStmt) -> ResolvedImport:
        """Files, bindings, and star modules of one import statement in ``importer``."""
        files: list[str] = []
        bindings: list[tuple[str, Target]] = []
        stars: list[ModuleRef] = []
        outcome: ImportOutcome
        if stmt.level == 0 and stmt.bindings and all(b.kind == "module" for b in stmt.bindings):
            outcomes: list[ImportOutcome] = []
            for binding in stmt.bindings:
                dotted = binding.name or stmt.module
                full = self.find_module(dotted, importer=importer)
                if full is not None:
                    outcomes.append("internal")
                    if full.file:
                        files.append(full.file)
                else:
                    absent = self._absent(dotted, importer)
                    outcomes.append("missing" if absent.kind == "missing" else "external")
                if binding.local:
                    bindings.append((binding.local, self.binding_target(importer, stmt, binding)))
            outcome = _combine(outcomes)
        else:
            base = self.base_module(importer, stmt)
            if base is None:
                absent = self._absent_from(importer, stmt)
                outcome = "missing" if absent.kind == "missing" else "external"
                bindings = [(b.local, absent) for b in stmt.bindings if b.local]
            else:
                outcome = "internal"
                for binding in stmt.bindings:
                    if binding.kind == "star":
                        stars.append(base)
                        files += [base.file] if base.file else []
                        continue
                    if not binding.local or not binding.name:
                        continue
                    sub = self.submodule(base, binding.name)
                    if sub is not None:
                        files += [sub.file] if sub.file else []
                        bindings.append((binding.local, Target.for_module(sub)))
                    else:
                        files += [base.file] if base.file else []
                        bindings.append((binding.local, self.lookup(base, binding.name)))
                if not files and all(target.kind == "missing" for _, target in bindings):
                    outcome = "missing"  # e.g. `from . import x` with no x in a namespace package
        unique = tuple(dict.fromkeys(path for path in files if path != importer))
        return ResolvedImport(outcome, unique, tuple(bindings), tuple(stars))

    def _absent(self, dotted: str, importer: str | None) -> Target:
        """External, unless the module's top-level package is in the repository (missing)."""
        head = dotted.split(".", 1)[0]
        if self.find_module(head, importer=importer) is not None:
            return Target.for_missing(dotted)
        return Target.for_external(dotted)

    def _absent_from(self, importer: str, stmt: ImportStmt) -> Target:
        if stmt.level > 0:
            return Target.for_missing(stmt.module)
        return self._absent(stmt.module, importer)


def _combine(outcomes: list[ImportOutcome]) -> ImportOutcome:
    if "internal" in outcomes:
        return "internal"
    return "missing" if "missing" in outcomes else "external"
