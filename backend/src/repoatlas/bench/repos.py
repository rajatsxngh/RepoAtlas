"""The benchmark's repos: the curated sandbox recipes and their files at the pinned commits."""

from __future__ import annotations

from pathlib import Path

from repoatlas.ingest.source import SourceTree, TreeEntry
from repoatlas.sandbox.paths import repo_root, sandbox_dir
from repoatlas.sandbox.recipe import Recipe, load_recipe


def benchmark_dir() -> Path:
    return repo_root() / "eval" / "benchmark"


def recipes() -> dict[str, Recipe]:
    """Every benchmark repo's recipe, by repo ID."""
    found = {}
    for path in sorted((sandbox_dir() / "recipes").glob("*.yaml")):
        recipe = load_recipe(path)
        found[recipe.id] = recipe
    return found


class RepoFiles:
    """Read-only access to one repo's files at its pinned commit, with per-file line caches."""

    def __init__(self, repo_id: str, commit: str, tree: SourceTree) -> None:
        self.repo_id = repo_id
        self.commit = commit
        self.tree = tree
        self._entries: dict[str, TreeEntry] = {e.path: e for e in tree.entries() if e.is_regular}
        self._lines: dict[str, list[str]] = {}

    def paths(self) -> list[str]:
        return sorted(self._entries)

    def has(self, path: str) -> bool:
        return path in self._entries

    def data(self, path: str) -> bytes:
        return self.tree.read(self._entries[path])

    def text(self, path: str) -> str:
        return self.data(path).decode("utf-8", "replace")

    def lines(self, path: str) -> list[str]:
        if path not in self._lines:
            self._lines[path] = self.text(path).splitlines()
        return self._lines[path]

    def line(self, path: str, line: int) -> str | None:
        """The 1-based ``line`` of ``path``, or None if the file or line does not exist."""
        if not self.has(path):
            return None
        lines = self.lines(path)
        return lines[line - 1] if 1 <= line <= len(lines) else None


def open_repo(recipe: Recipe) -> RepoFiles:
    """The recipe's commit, from the sandbox's local git cache (fetched on first use)."""
    from repoatlas.sandbox.images import open_source

    return RepoFiles(recipe.id, recipe.commit, open_source(recipe))
