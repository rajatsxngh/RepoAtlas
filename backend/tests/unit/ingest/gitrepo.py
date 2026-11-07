"""Build throwaway git repositories with exact trees, without ever checking files out.

Trees are assembled with ``hash-object`` / ``mktree`` / ``commit-tree``, so tests can include
content and names a Windows working tree could not hold (CRLF bytes, ``aux.py``, case clashes,
symlinks, submodules).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repoatlas.ingest.git import run_git

IDENTITY = ["-c", "user.name=RepoAtlas Tests", "-c", "user.email=tests@example.invalid"]


@dataclass(frozen=True)
class Spec:
    """A file to place in the tree: ``mode`` 100644/100755/120000 (data) or 160000 (gitlink)."""

    path: str
    data: bytes = b""
    mode: str = "100644"
    gitlink: str | None = None


class OriginRepo:
    """A bare repository that tests fetch from through a ``file://`` URL."""

    def __init__(self, path: Path) -> None:
        self.path = path
        run_git(["init", "--bare", "-q", str(path)])
        run_git(["config", "uploadpack.allowAnySHA1InWant", "true"], cwd=path)
        run_git(["config", "uploadpack.allowFilter", "true"], cwd=path)
        self.head: str | None = None

    @property
    def url(self) -> str:
        return self.path.resolve().as_uri()

    def blob(self, data: bytes) -> str:
        return run_git(["hash-object", "-w", "--stdin"], cwd=self.path, input=data).decode().strip()

    def commit(self, files: list[Spec], message: str = "commit") -> str:
        tree = self._tree(files)
        args = [*IDENTITY, "commit-tree", tree, "-m", message]
        if self.head:
            args += ["-p", self.head]
        self.head = run_git(args, cwd=self.path).decode().strip()
        run_git(["update-ref", "refs/heads/main", self.head], cwd=self.path)
        return self.head

    def _tree(self, files: list[Spec], prefix: str = "") -> str:
        lines: list[str] = []
        children: dict[str, list[Spec]] = {}
        for spec in files:
            rest = spec.path[len(prefix) :]
            head, sep, _tail = rest.partition("/")
            if sep:
                children.setdefault(head, []).append(spec)
            elif spec.mode == "160000":
                lines.append(f"160000 commit {spec.gitlink}\t{rest}")
            else:
                lines.append(f"{spec.mode} blob {self.blob(spec.data)}\t{rest}")
        for name, nested in children.items():
            lines.append(f"040000 tree {self._tree(nested, prefix + name + '/')}\t{name}")
        listing = "".join(line + "\0" for line in lines).encode("utf-8")
        return run_git(["mktree", "-z", "--missing"], cwd=self.path, input=listing).decode().strip()
