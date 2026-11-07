"""Public markers (GRA-8): what users of a package reach, marked ``meta.public = true``.

- Route nodes (``graph.routes`` marks them).
- Names in the ``__all__`` of a top-level package's ``__init__.py`` (a package directly under a
  Python root), resolved to where they are defined.
- Exports of the files a ``package.json`` names in ``main``, ``module``, or ``exports``
  (conditions and subpaths included; ``*`` patterns and type declarations skipped), following
  ``export *`` chains. An entry that points at build output (``dist/``, ``lib/``, ``build/``,
  ``out/``, ``esm/``, ``cjs/``) falls back to the same path under ``src/``, then to the package
  root, with or without its JavaScript extension.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from repoatlas.graph.keys import parent_dir
from repoatlas.graph.model import Node
from repoatlas.graph.project import ProjectFiles, join
from repoatlas.graph.python_resolver import PythonModuleResolver
from repoatlas.graph.targets import ModuleRef
from repoatlas.graph.ts_resolver import TsModuleResolver
from repoatlas.parsing.models import ParsedFile

BUILD_FOLDERS = frozenset({"dist", "lib", "build", "out", "esm", "cjs"})
_JS_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx")
_TYPE_CONDITIONS = frozenset({"types", "typings"})


def _folder(path: str) -> str:
    folder = parent_dir(path)
    return "" if folder == "." else folder


def public_symbols(
    files: Sequence[ParsedFile],
    python: PythonModuleResolver,
    typescript: TsModuleResolver,
    project: ProjectFiles,
) -> set[str]:
    """Keys of the symbols a package makes public."""
    keys: set[str] = set()
    for parsed in files:
        if parsed.language != "py" or not parsed.path.endswith("__init__.py") or not parsed.exports:
            continue
        package = _folder(parsed.path)
        if not package or _folder(package) not in python.roots:
            continue
        module = ModuleRef(parsed.path, package)
        for export in parsed.exports:
            target = python.lookup(module, export.name)
            if target.kind == "symbol" and target.key is not None:
                keys.add(target.key)

    for path, text in project.named("package.json"):
        if "node_modules" in path.split("/"):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        for spec in package_entries(data):
            entry = resolve_entry(typescript, _folder(path), spec)
            if entry is None:
                continue
            for name in sorted(typescript.exported_names(entry)):
                target = typescript.lookup_export(entry, name)
                if target.kind == "symbol" and target.key is not None:
                    keys.add(target.key)
    return keys


def package_entries(data: dict[str, Any]) -> list[str]:
    """Entry paths a ``package.json`` names, in order, without duplicates."""
    entries: list[str] = []
    for name in ("main", "module"):
        if isinstance(data.get(name), str):
            entries.append(data[name])
    entries += _export_targets(data.get("exports"))
    usable = [
        e
        for e in entries
        if "*" not in e and not e.endswith((".d.ts", ".d.mts", ".d.cts", ".json"))
    ]
    return list(dict.fromkeys(usable))


def _export_targets(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [target for item in value for target in _export_targets(item)]
    if isinstance(value, dict):
        return [
            target
            for key, item in value.items()
            if key not in _TYPE_CONDITIONS
            for target in _export_targets(item)
        ]
    return []


def resolve_entry(typescript: TsModuleResolver, folder: str, spec: str) -> str | None:
    """The parsed source file a package entry names (see the module docstring)."""
    importer = join(folder, "package.json")
    relative = join(spec)
    parts = relative.split("/") if relative else []
    candidates = [relative]
    if parts and parts[0] in BUILD_FOLDERS:
        candidates += ["/".join(["src", *parts[1:]]), "/".join(parts[1:])]
    for candidate in candidates:
        options = [candidate]
        if candidate.endswith(_JS_EXTENSIONS):
            options.append(candidate.rsplit(".", 1)[0])
        for option in options:
            _outcome, found = typescript.resolve_module(importer, f"./{option}")
            if found is not None and typescript.is_parsed(found):
                return found
    return None


def mark_public(nodes: dict[str, Node], keys: Iterable[str]) -> None:
    for key in keys:
        node = nodes.get(key)
        if node is not None and node.meta.get("public") is not True:
            nodes[key] = node.model_copy(update={"meta": {**node.meta, "public": True}})
