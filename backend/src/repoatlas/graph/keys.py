"""Keys for nodes that are not parse symbols (PRD §7.3): directories, files, and routes."""

from __future__ import annotations

from repoatlas.parsing.symbols import encode_part

ROOT_DIR = "."


def dir_key(path: str) -> str:
    """``dir:<path>``; the repository root is ``dir:.``."""
    return f"dir:{encode_part(path or ROOT_DIR)}"


def file_key(path: str) -> str:
    return f"file:{encode_part(path)}"


def route_key(path: str, method: str, pattern: str) -> str:
    """``route:<path>:<METHOD> <pattern>``, e.g. ``route:src/app.py:GET /carts/{id}``."""
    return f"route:{encode_part(path)}:{encode_part(f'{method} {pattern}')}"


def parent_dir(path: str) -> str:
    """The directory holding ``path`` (``.`` at the top level)."""
    head, sep, _tail = path.rpartition("/")
    return head if sep else ROOT_DIR


def ancestors(path: str) -> list[str]:
    """Directories from the root down to the one holding ``path``: ``a/b/c.py`` → ``., a, a/b``."""
    parts = path.split("/")[:-1]
    return [ROOT_DIR, *("/".join(parts[: i + 1]) for i in range(len(parts)))]
