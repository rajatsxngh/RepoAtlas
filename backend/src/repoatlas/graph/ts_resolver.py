"""TypeScript/JavaScript module resolution (GRA-4): specifiers → repository files and symbols.

- Relative specifiers try the exact path; then, for ``.js``-family specifiers, the TypeScript
  source with the same base name (``.js`` → ``.ts``/``.tsx``, ``.jsx`` → ``.tsx``, ``.mjs`` →
  ``.mts``, ``.cjs`` → ``.cts``); then each extension in ``EXTENSIONS``; then ``index.*``.
- Bare specifiers go through the nearest ``tsconfig.json``/``jsconfig.json``: an exact ``paths``
  pattern, else the wildcard pattern with the longest prefix (each target tried in order), then
  ``baseUrl``. Anything else is an external package.
- A relative specifier naming an asset (``./app.css``, ``./data.json``) is external; any other
  relative specifier that matches no file is missing.
- ``lookup_export`` finds where an exported name is defined, following ``export … from``,
  ``export * from``, and import-then-export chains for up to ``MAX_REEXPORT_DEPTH`` hops.
  ``export *`` never forwards ``default``. CommonJS ``exports.x``/``module.exports`` count as
  exports (``module.exports = f`` is the default export).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from repoatlas.graph.keys import parent_dir
from repoatlas.graph.project import TsConfig, join
from repoatlas.graph.targets import ImportOutcome, ModuleRef, ResolvedImport, Target
from repoatlas.ingest.inventory import language_for
from repoatlas.parsing.models import Export, ImportBinding, ImportStmt, ParsedFile, Symbol

MAX_REEXPORT_DEPTH = 3
EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts")
_SOURCE_SWAPS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
_ASSET_NAMES = (
    "css scss sass less styl json json5 svg png jpg jpeg gif webp avif ico bmp woff woff2 ttf "
    "otf eot mp3 mp4 wav webm ogg txt md mdx html htm vue svelte astro graphql gql yaml yml "
    "toml wasm node glsl csv xml pdf"
)
_ASSET_EXTENSIONS = frozenset(f".{name}" for name in _ASSET_NAMES.split(" "))

_Binding = tuple[ImportStmt, ImportBinding]


def _folder(path: str) -> str:
    folder = parent_dir(path)
    return "" if folder == "." else folder


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


class TsModuleResolver:
    def __init__(
        self,
        files: Iterable[ParsedFile],
        paths: Iterable[str],
        *,
        configs: Mapping[str, TsConfig] | None = None,
    ) -> None:
        """``files``: parsed TS/JS files; ``paths``: every inventoried path (skipped included);
        ``configs``: ``graph.project.ts_configs`` (folder → config)."""
        self._paths = {path for path in paths if language_for(path) in ("ts", "js")}
        self._configs = dict(configs or {})
        self._parsed: dict[str, ParsedFile] = {}
        self._top: dict[str, dict[str, Symbol]] = {}
        self._exports: dict[str, dict[str, Export]] = {}
        self._reexports: dict[str, dict[str, _Binding]] = {}
        self._stars: dict[str, list[ImportStmt]] = {}
        self._imports: dict[str, dict[str, _Binding]] = {}
        for parsed in files:
            if parsed.language not in ("ts", "js"):
                continue
            path = parsed.path
            self._parsed[path] = parsed
            self._top[path] = {
                s.name: s
                for s in parsed.symbols
                if s.parent is None and s.kind != "test" and s.qualname == s.name
            }
            self._exports[path] = {e.name: e for e in parsed.exports if e.local is not None}
            reexports: dict[str, _Binding] = {}
            stars: list[ImportStmt] = []
            imports: dict[str, _Binding] = {}
            for stmt in parsed.imports:
                for binding in stmt.bindings:
                    if stmt.kind == "reexport":
                        if binding.kind == "star":
                            stars.append(stmt)
                        elif binding.local:
                            reexports[binding.local] = (stmt, binding)
                    elif stmt.scope is None and binding.local:
                        imports[binding.local] = (stmt, binding)
            self._reexports[path] = reexports
            self._stars[path] = stars
            self._imports[path] = imports

    # -- modules ---------------------------------------------------------------------------

    def resolve_module(self, importer: str, specifier: str) -> tuple[ImportOutcome, str | None]:
        """The repository file ``specifier`` names when imported from ``importer``."""
        if specifier.startswith("."):
            found = self._file(join(_folder(importer), specifier))
            if found is not None:
                return "internal", found
            if _extension(specifier) in _ASSET_EXTENSIONS:
                return "external", None
            return "missing", None
        if not specifier or specifier.startswith("/"):
            return "missing", None
        config = self.config_for(importer)
        if config is not None:
            for target in _path_targets(config, specifier):
                found = self._file(target)
                if found is not None:
                    return "internal", found
            if config.base_url is not None:
                found = self._file(join(config.base_url, specifier))
                if found is not None:
                    return "internal", found
        return "external", None

    def is_parsed(self, path: str) -> bool:
        """Whether ``path`` is a parsed TS/JS file (not skipped by the inventory)."""
        return path in self._parsed

    def config_for(self, path: str) -> TsConfig | None:
        """The nearest config at or above ``path``'s folder."""
        folder = _folder(path)
        while True:
            config = self._configs.get(folder)
            if config is not None:
                return config
            if not folder:
                return None
            folder = _folder(folder)

    def _file(self, base: str) -> str | None:
        if base in self._paths:
            return base
        extension = _extension(base)
        for swap in _SOURCE_SWAPS.get(extension, ()):
            candidate = base[: -len(extension)] + swap
            if candidate in self._paths:
                return candidate
        for ext in EXTENSIONS:
            if base and base + ext in self._paths:
                return base + ext
        for ext in EXTENSIONS:
            candidate = join(base, f"index{ext}")
            if candidate in self._paths:
                return candidate
        return None

    # -- exports ---------------------------------------------------------------------------

    def lookup_export(self, path: str, name: str, depth: int = 0) -> Target:
        """What module ``path`` exports as ``name``; a ``member`` when it isn't a symbol (types,
        interfaces, and values that aren't functions or classes) or isn't exported at all."""
        found = self._find_export(path, name, depth)
        return found if found is not None else Target.for_member(path, name)

    def exported_names(self, path: str, depth: int = 0) -> set[str]:
        """Every name ``path`` exports, including names forwarded by ``export *``."""
        if path not in self._parsed:
            return set()
        names = set(self._exports[path]) | set(self._reexports[path])
        if depth < MAX_REEXPORT_DEPTH:
            for stmt in self._stars[path]:
                _outcome, target = self.resolve_module(path, stmt.module)
                if target is not None and target != path:
                    names |= self.exported_names(target, depth + 1) - {"default"}
        return names

    def _find_export(self, path: str, name: str, depth: int) -> Target | None:
        if path not in self._parsed:
            return Target.for_external(path)  # a file the inventory skipped
        export = self._exports[path].get(name)
        if export is not None and export.local is not None:
            symbol = self._top[path].get(export.local)
            if symbol is not None:
                return Target.for_symbol(symbol.key, path)
            imported = self._imports[path].get(export.local)
            if imported is not None and depth < MAX_REEXPORT_DEPTH:
                stmt, binding = imported
                return self.binding_target(path, stmt, binding, depth + 1)
            return Target.for_member(path, export.local)
        if depth >= MAX_REEXPORT_DEPTH:
            return None
        forwarded = self._reexports[path].get(name)
        if forwarded is not None:
            stmt, binding = forwarded
            outcome, target = self.resolve_module(path, stmt.module)
            if target is None:
                return _absent(outcome, stmt.module)
            if binding.kind == "namespace":
                return Target.for_module(ModuleRef(target))
            return self.lookup_export(target, binding.name or name, depth + 1)
        if name != "default":
            for stmt in self._stars[path]:
                _outcome, target = self.resolve_module(path, stmt.module)
                if target is not None and target != path:
                    found = self._find_export(target, name, depth + 1)
                    if found is not None:
                        return found
        return None

    # -- imports ---------------------------------------------------------------------------

    def binding_target(
        self, importer: str, stmt: ImportStmt, binding: ImportBinding, depth: int = 0
    ) -> Target:
        """The target of one binding of an import statement in ``importer``."""
        outcome, target = self.resolve_module(importer, stmt.module)
        if target is None:
            return _absent(outcome, stmt.module)
        if binding.kind in ("namespace", "module"):  # import * as ns · const m = require(…)
            return Target.for_module(ModuleRef(target))
        if binding.kind == "default":
            return self.lookup_export(target, "default", depth)
        return self.lookup_export(target, binding.name or "", depth)

    def resolve_import(self, importer: str, stmt: ImportStmt) -> ResolvedImport:
        """Files and local bindings of one import (re-exports bind no local names)."""
        outcome, target = self.resolve_module(importer, stmt.module)
        files = (target,) if target is not None and target != importer else ()
        bindings: list[tuple[str, Target]] = []
        if stmt.kind != "reexport":
            for binding in stmt.bindings:
                if binding.local and binding.kind != "star":
                    bindings.append((binding.local, self.binding_target(importer, stmt, binding)))
        return ResolvedImport(outcome, files, tuple(bindings))


def _absent(outcome: ImportOutcome, specifier: str) -> Target:
    if outcome == "external":
        return Target.for_external(specifier)
    return Target.for_missing(specifier)


def _path_targets(config: TsConfig, specifier: str) -> list[str]:
    """``paths`` targets for ``specifier``: an exact pattern wins, else the longest prefix."""
    best: tuple[int, str, tuple[str, ...]] | None = None
    for pattern, targets in config.paths:
        if "*" not in pattern:
            if pattern == specifier:
                return list(targets)
            continue
        prefix, _star, suffix = pattern.partition("*")
        fits = len(specifier) >= len(prefix) + len(suffix)
        if fits and specifier.startswith(prefix) and specifier.endswith(suffix):
            captured = specifier[len(prefix) : len(specifier) - len(suffix)]
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), captured, targets)
    if best is None:
        return []
    _length, captured, targets = best
    return [target.replace("*", captured, 1) for target in targets]
