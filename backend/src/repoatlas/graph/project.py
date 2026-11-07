"""Project configuration the resolvers read: ``pyproject.toml`` and ``tsconfig.json``.

These files are not source code, so the inventory does not list them; the pipeline reads them
from the source tree into ``ProjectFiles`` and ``build_graph`` interprets them. Malformed files
are ignored, never fatal.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from repoatlas.graph.keys import parent_dir

PYPROJECT = "pyproject.toml"
TS_CONFIGS = ("tsconfig.json", "jsconfig.json")  # the configs that apply to a folder, in priority


class ProjectFiles(BaseModel):
    """Configuration files by repository path (text decoded as UTF-8)."""

    model_config = ConfigDict(frozen=True)

    files: dict[str, str] = {}

    def named(self, *names: str) -> list[tuple[str, str]]:
        """Files whose base name is one of ``names``, sorted by path."""
        return [
            (path, text)
            for path, text in sorted(self.files.items())
            if path.rsplit("/", 1)[-1] in names
        ]


def join(*parts: str) -> str:
    """Join repository paths, normalizing ``.`` and ``..`` segments ('' is the root)."""
    segments: list[str] = []
    for part in parts:
        for segment in part.replace("\\", "/").split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if segments:
                    segments.pop()
                continue
            segments.append(segment)
    return "/".join(segments)


def python_roots(project: ProjectFiles, python_paths: Iterable[str]) -> list[str]:
    """Directories that absolute Python imports resolve against, in priority order.

    The repository root and ``src/`` come first, then for each ``pyproject.toml`` its own
    directory, its ``src/``, and the package directories its build settings name (setuptools
    ``package-dir`` and ``packages.find.where``, Poetry ``packages``, Hatch wheel ``packages``,
    PDM ``package-dir``, maturin ``python-source``). Only directories that hold Python files count.
    """
    directories: set[str] = set()
    for path in python_paths:
        current = parent_dir(path)
        while current != ".":
            directories.add(current)
            current = parent_dir(current)
    candidates = ["", "src"]
    for path, text in project.named(PYPROJECT):
        base = "" if parent_dir(path) == "." else parent_dir(path)
        candidates += [base, join(base, "src")]
        candidates += [join(base, rel) for rel in _package_dirs(text)]
    roots: list[str] = []
    for root in candidates:
        if root not in roots and (root == "" or root in directories):
            roots.append(root)
    return roots


def _package_dirs(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeError):
        return []
    tool = _table(data.get("tool"))
    found: list[str] = []
    setuptools = _table(tool.get("setuptools"))
    for package, location in _table(setuptools.get("package-dir")).items():
        if not isinstance(location, str):
            continue
        if package == "":
            found.append(location)
        elif location.rstrip("/").rsplit("/", 1)[-1] == package.rsplit(".", 1)[-1]:
            found.append(location.rstrip("/").rpartition("/")[0])
    find = _table(_table(setuptools.get("packages")).get("find"))
    found += [where for where in _list(find.get("where")) if isinstance(where, str)]
    for entry in _list(_table(tool.get("poetry")).get("packages")):
        if isinstance(entry, dict) and isinstance(entry.get("from", ""), str):
            found.append(entry.get("from", ""))
    wheel = _table(
        _table(_table(_table(tool.get("hatch")).get("build")).get("targets")).get("wheel")
    )
    for package in _list(wheel.get("packages")):
        if isinstance(package, str):
            found.append(package.rstrip("/").rpartition("/")[0])
    for value in (
        _table(_table(tool.get("pdm")).get("build")).get("package-dir"),
        _table(tool.get("maturin")).get("python-source"),
    ):
        if isinstance(value, str):
            found.append(value)
    return found


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# -- TypeScript and JavaScript -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TsConfig:
    """The module-resolution options of one ``tsconfig.json``/``jsconfig.json``.

    ``base_url`` and every ``paths`` target are repository paths ('' is the root): ``baseUrl`` is
    relative to the config that sets it; ``paths`` targets to the effective ``baseUrl``, or to the
    config that sets ``paths`` when there is none.
    """

    path: str
    base_url: str | None = None
    paths: tuple[tuple[str, tuple[str, ...]], ...] = ()


def load_jsonc(text: str) -> Any:
    """Parse JSON with comments and trailing commas (as ``tsconfig.json`` allows); ``None`` if
    the text is not valid even then."""
    try:
        return json.loads(_strip_trailing_commas(_strip_comments(text.lstrip("\ufeff"))))
    except ValueError:
        return None


def ts_configs(project: ProjectFiles) -> dict[str, TsConfig]:
    """The config that applies to each folder holding one, keyed by folder ('' is the root).

    ``tsconfig.json`` wins over ``jsconfig.json`` in the same folder. One level of ``extends`` is
    followed when it names a config file in the repository by relative path; package configs
    (``@tsconfig/node20``) are ignored.
    """
    configs: dict[str, TsConfig] = {}
    for name in reversed(TS_CONFIGS):
        for path, text in project.named(name):
            folder = "" if parent_dir(path) == "." else parent_dir(path)
            data = load_jsonc(text)
            if isinstance(data, dict):
                configs[folder] = _ts_config(path, folder, data, project)
    return dict(sorted(configs.items()))


def _ts_config(path: str, folder: str, data: dict[str, Any], project: ProjectFiles) -> TsConfig:
    base_url: str | None = None
    paths: dict[str, Any] = {}
    paths_from = folder
    layers = [(path, folder, data)]
    parent = _extended(folder, data.get("extends"), project)
    if parent is not None:
        layers.insert(0, parent)
    for _layer_path, layer_folder, layer in layers:
        options = layer.get("compilerOptions")
        if not isinstance(options, dict):
            continue
        if isinstance(options.get("baseUrl"), str):
            base_url = join(layer_folder, options["baseUrl"])
        if isinstance(options.get("paths"), dict):
            paths = options["paths"]
            paths_from = layer_folder
    anchor = base_url if base_url is not None else paths_from
    mapped: list[tuple[str, tuple[str, ...]]] = []
    for pattern, targets in paths.items():
        if isinstance(pattern, str) and isinstance(targets, list):
            joined = tuple(join(anchor, t) for t in targets if isinstance(t, str))
            mapped.append((pattern, joined))
    return TsConfig(path=path, base_url=base_url, paths=tuple(mapped))


def _extended(
    folder: str, extends: Any, project: ProjectFiles
) -> tuple[str, str, dict[str, Any]] | None:
    names = extends if isinstance(extends, list) else [extends]
    for name in names:  # TypeScript 5 accepts a list; the first config found is used
        if not isinstance(name, str) or not name.startswith("."):
            continue
        target = join(folder, name)
        for candidate in (target, f"{target}.json"):
            text = project.files.get(candidate)
            data = load_jsonc(text) if text is not None else None
            if isinstance(data, dict):
                parent = parent_dir(candidate)
                return candidate, "" if parent == "." else parent, data
    return None


def _strip_comments(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("//", i):
            newline = text.find("\n", i)
            i = n if newline < 0 else newline
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = n if close < 0 else close + 2
            out.append(" ")
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i : end + 1])
            i = end + 1
            continue
        if char == ",":
            ahead = i + 1
            while ahead < n and text[ahead] in " \t\r\n":
                ahead += 1
            if ahead < n and text[ahead] in "}]":
                i += 1
                continue
        out.append(char)
        i += 1
    return "".join(out)
