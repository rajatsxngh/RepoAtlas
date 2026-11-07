"""Fetch a commit's files and its history without writing a working tree (decision 0002)."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.git import run_git
from repoatlas.ingest.source import GitSourceTree

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ALLOWED_URL_PREFIXES = ("https://github.com/", "file://")


def fetch_source(
    clone_url: str, commit: str, dest: Path, *, label: str, timeout: float = 300.0
) -> GitSourceTree:
    """Fetch only ``commit``'s snapshot (depth 1) into a new bare repository at ``dest``."""
    _validate(clone_url, commit)
    run_git(["init", "--bare", "-q", str(dest)], timeout=timeout)
    run_git(["fetch", "--depth=1", "--no-tags", "-q", clone_url, commit], cwd=dest, timeout=timeout)
    run_git(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=dest, timeout=timeout)
    return GitSourceTree(dest, commit, label=label, timeout=timeout)


@dataclass(frozen=True, slots=True)
class HistoryStore:
    """Commits and trees (no file contents) up to ``depth`` generations behind ``commit``.

    Queries that compare trees must pass ``--no-renames``: rename detection reads file contents,
    which a blobless repository downloads one by one.
    """

    path: Path
    commit: str
    timeout: float = 300.0

    def git(self, *args: str) -> str:
        return run_git(args, cwd=self.path, timeout=self.timeout).decode("utf-8", "replace")

    def commit_count(self) -> int:
        return int(self.git("rev-list", "--count", self.commit).strip())


def fetch_history(
    clone_url: str, commit: str, dest: Path, *, depth: int = 1000, timeout: float = 300.0
) -> HistoryStore:
    """Fetch history behind ``commit`` as a blobless partial clone at ``dest``."""
    _validate(clone_url, commit)
    if depth < 1:
        raise FetchError("History depth must be at least 1.")
    run_git(["init", "--bare", "-q", str(dest)], timeout=timeout)
    for key, value in (
        ("core.repositoryformatversion", "1"),
        ("extensions.partialClone", "origin"),
    ):
        run_git(["config", key, value], cwd=dest, timeout=timeout)
    run_git(["remote", "add", "origin", clone_url], cwd=dest, timeout=timeout)
    run_git(["config", "remote.origin.promisor", "true"], cwd=dest, timeout=timeout)
    run_git(["config", "remote.origin.partialclonefilter", "blob:none"], cwd=dest, timeout=timeout)
    run_git(
        ["fetch", f"--depth={depth}", "--filter=blob:none", "--no-tags", "-q", "origin", commit],
        cwd=dest,
        timeout=timeout,
    )
    return HistoryStore(path=dest, commit=commit, timeout=timeout)


class Workspace:
    """A temporary directory for fetches, removed on exit unless ``keep`` is set."""

    def __init__(self, base_dir: Path, *, keep: bool = False) -> None:
        base_dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix="fetch-", dir=base_dir))
        self.keep = keep

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.keep:
            remove_tree(self.path)


def remove_tree(path: Path) -> None:
    """Delete a directory tree, including git's read-only pack files on Windows."""

    def make_writable_and_retry(func: Callable[[str], object], target: str, _exc: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onexc=make_writable_and_retry)


def _validate(clone_url: str, commit: str) -> None:
    if not _SHA_RE.fullmatch(commit):
        raise FetchError("A full 40-character commit SHA is required.")
    if not clone_url.startswith(_ALLOWED_URL_PREFIXES):
        raise FetchError("Only https://github.com/ and file:// repositories can be fetched.")
