"""Project configuration files the resolvers read: ``pyproject.toml`` (package roots).

These files are not source code, so the inventory does not list them; the pipeline reads them
from the source tree into ``ProjectFiles`` and ``build_graph`` interprets them. Malformed files
are ignored, never fatal.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict

from repoatlas.graph.keys import parent_dir

PYPROJECT = "pyproject.toml"


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
