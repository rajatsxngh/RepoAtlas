"""Source trees: the files of one commit, read as raw bytes.

``GitSourceTree`` reads from a bare repository (``git ls-tree`` + ``git cat-file --batch``), so no
working tree is ever written. ``DirectorySourceTree`` reads a plain directory (fixtures, local
development). Both report every file with its git blob OID (decision 0002).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from repoatlas.ingest.errors import FetchError
from repoatlas.ingest.git import git_command, git_env, run_git

REGULAR_MODES = frozenset({"100644", "100755"})
SYMLINK_MODE = "120000"
GITLINK_MODE = "160000"


def blob_oid(data: bytes) -> str:
    """Git's object ID for a blob with these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """One file in a tree. ``size`` is ``None`` for submodules."""

    path: str
    mode: str
    oid: str
    size: int | None

    @property
    def is_regular(self) -> bool:
        return self.mode in REGULAR_MODES


class SourceTree:
    """Base class: ``entries()`` lists files sorted by path; ``read()`` returns raw bytes."""

    label: str
    commit: str | None

    def entries(self) -> list[TreeEntry]:
        raise NotImplementedError

    def read(self, entry: TreeEntry) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        """Release resources (processes, file handles)."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class GitSourceTree(SourceTree):
    """Files of ``commit`` in a bare repository."""

    def __init__(self, repo_dir: Path, commit: str, *, label: str, timeout: float = 300.0) -> None:
        self.repo_dir = repo_dir
        self.commit = commit
        self._commit = commit
        self.label = label
        self._timeout = timeout
        self._entries: list[TreeEntry] | None = None
        self._reader: _BlobReader | None = None

    def entries(self) -> list[TreeEntry]:
        if self._entries is None:
            listing = run_git(
                ["ls-tree", "-r", "-l", "-z", "--full-tree", self._commit],
                cwd=self.repo_dir,
                timeout=self._timeout,
            )
            self._entries = sorted(_parse_ls_tree(listing), key=lambda entry: entry.path)
        return self._entries

    def read(self, entry: TreeEntry) -> bytes:
        if not entry.is_regular and entry.mode != SYMLINK_MODE:
            raise FetchError(f"{entry.path} is not a file.")
        if self._reader is None:
            self._reader = _BlobReader(self.repo_dir)
        return self._reader.read(entry.oid)

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


class DirectorySourceTree(SourceTree):
    """Files under a local directory (``.git`` folders are ignored; modes are always 100644)."""

    def __init__(self, root: Path, *, label: str | None = None) -> None:
        self.root = root.resolve()
        self.label = label or f"dir:{self.root.name}"
        self.commit = None
        self._entries: list[TreeEntry] | None = None

    def entries(self) -> list[TreeEntry]:
        if self._entries is None:
            found: list[TreeEntry] = []
            for current, dirs, files in os.walk(self.root):
                dirs[:] = sorted(d for d in dirs if d != ".git")
                for name in files:
                    path = Path(current) / name
                    rel = path.relative_to(self.root).as_posix()
                    if path.is_symlink():
                        target = os.readlink(path).encode("utf-8", "replace")
                        found.append(TreeEntry(rel, SYMLINK_MODE, blob_oid(target), len(target)))
                    else:
                        data = path.read_bytes()
                        found.append(TreeEntry(rel, "100644", blob_oid(data), len(data)))
            self._entries = sorted(found, key=lambda entry: entry.path)
        return self._entries

    def read(self, entry: TreeEntry) -> bytes:
        path = self.root / entry.path
        if entry.mode == SYMLINK_MODE:
            return os.readlink(path).encode("utf-8", "replace")
        return path.read_bytes()


def _parse_ls_tree(listing: bytes) -> list[TreeEntry]:
    """Parse ``git ls-tree -r -l -z`` records: ``<mode> <type> <oid> <size>\\t<path>\\0``."""
    entries: list[TreeEntry] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, _object_type, oid, size = meta.decode("ascii").split()
        entries.append(
            TreeEntry(
                path=raw_path.decode("utf-8", "replace"),
                mode=mode,
                oid=oid,
                size=None if size == "-" else int(size),
            )
        )
    return entries


class _BlobReader:
    """A long-running ``git cat-file --batch`` process, queried one object at a time."""

    def __init__(self, repo_dir: Path) -> None:
        self._proc = subprocess.Popen(
            git_command(["cat-file", "--batch"]),
            cwd=repo_dir,
            env=git_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def read(self, oid: str) -> bytes:
        stdin, stdout = self._pipes()
        stdin.write(oid.encode("ascii") + b"\n")
        stdin.flush()
        header = stdout.readline().decode("ascii", "replace").split()
        if len(header) != 3 or header[0] != oid:
            raise FetchError(f"Object {oid} is missing from the fetched commit.")
        size = int(header[2])
        data = stdout.read(size)
        stdout.read(1)  # the newline after each object
        if len(data) != size:
            raise FetchError(f"Object {oid} was truncated.")
        return data

    def close(self) -> None:
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self._proc.kill()
            self._proc.wait()
        if self._proc.stdout is not None:
            self._proc.stdout.close()

    def _pipes(self) -> tuple[IO[bytes], IO[bytes]]:
        if self._proc.stdin is None or self._proc.stdout is None:  # pragma: no cover
            raise FetchError("git cat-file is not running.")
        return self._proc.stdin, self._proc.stdout
