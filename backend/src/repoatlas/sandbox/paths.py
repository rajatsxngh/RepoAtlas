"""Where the sandbox's files live in the repository: ``sandbox/`` and ``eval/artifacts/``."""

from __future__ import annotations

from pathlib import Path

_MARKER = Path("sandbox") / "proxy" / "squid.conf"


def repo_root() -> Path:
    """The RepoAtlas checkout: the working directory or a parent that has ``sandbox/``."""
    here = Path(__file__).resolve()
    for directory in (Path.cwd().resolve(), *Path.cwd().resolve().parents, *here.parents):
        if (directory / _MARKER).is_file():
            return directory
    raise FileNotFoundError("Could not find the RepoAtlas checkout (no sandbox/proxy/squid.conf).")


def sandbox_dir() -> Path:
    return repo_root() / "sandbox"


def artifacts_dir(repo_id: str, commit: str) -> Path:
    """``eval/artifacts/<repo_id>/<commit>/``, created on first use."""
    path = repo_root() / "eval" / "artifacts" / repo_id / commit
    path.mkdir(parents=True, exist_ok=True)
    return path
