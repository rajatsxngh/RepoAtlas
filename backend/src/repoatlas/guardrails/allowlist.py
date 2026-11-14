"""Allowlists for reading code (GRD-7): only files of the snapshot, only valid arguments.

Every code read names a snapshot and a path. ``ensure_snapshot_path`` is the one gate such a
read goes through: the path must have the shape of a repository path (relative, POSIX, no
``.`` or ``..`` segments, no backslashes, drive letters, or control characters), and it must be
in the snapshot's own file list. Nothing is ever resolved against a file system, so there is no
directory to escape from: a path is a key into the snapshot's ``files`` rows, and the text
comes from the ``blobs`` table.

The other helpers validate arguments the same way for any caller that takes them from outside
(the API today; any tool that reads code): line ranges, symbol keys, blob OIDs, limits, and
free text. They raise the store's errors (``PathNotInSnapshot``, ``InvalidQuery``), which the
API already maps to 404 and 422.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from repoatlas.store.errors import InvalidQuery, PathNotInSnapshot

MAX_PATH_CHARS = 4_096  # the API's limits (repoatlas.api.schemas)
MAX_KEY_CHARS = 4_096
MAX_LINE = 2**31 - 1  # Postgres integer
_DRIVE = re.compile(r"^[A-Za-z]:")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_OID = re.compile(r"[0-9a-f]{40}")
_KEY_PREFIXES = ("py:", "ts:", "file:", "dir:", "route:")


@dataclass(frozen=True)
class SnapshotFile:
    """A file of a snapshot, as its ``files`` row describes it. ``line_count`` is ``None`` when
    the file's text is not stored (it was skipped: binary, too large, …)."""

    id: int
    path: str
    blob_oid: str
    line_count: int | None
    skip_reason: str | None


# (snapshot ID, path) → the file's row, or None when the snapshot has no such file.
FileLookup = Callable[[int, str], SnapshotFile | None]


def path_problem(path: str) -> str | None:
    """Why ``path`` can't be a file of any snapshot, or ``None`` when its shape is fine."""
    if not path:
        return "is empty"
    if len(path) > MAX_PATH_CHARS:
        return f"is longer than {MAX_PATH_CHARS} characters"
    if _CONTROL.search(path):
        return "contains control characters"
    if "\\" in path:
        return "contains a backslash (paths use forward slashes)"
    if path.startswith("/") or _DRIVE.match(path):
        return "is absolute"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return "has an empty, '.', or '..' segment"
    return None


def ensure_snapshot_path(snapshot_id: int, path: str, *, lookup: FileLookup) -> SnapshotFile:
    """The snapshot's file at ``path``, or ``PathNotInSnapshot``. The shape is checked before
    anything is looked up, so a malformed path never reaches a query."""
    problem = path_problem(path)
    if problem is not None:
        raise PathNotInSnapshot(f"Snapshot {snapshot_id} has no file {path[:200]!r}: it {problem}.")
    found = lookup(snapshot_id, path)
    if found is None:
        raise PathNotInSnapshot(f"Snapshot {snapshot_id} has no file {path!r}.")
    return found


def validate_line_range(
    start: int,
    end: int,
    *,
    line_count: int | None = None,
    max_lines: int | None = None,
    path: str = "the file",
) -> tuple[int, int]:
    """The 1-based inclusive range to read. ``start`` below 1 becomes 1; ``end`` past the
    file's end becomes its last line; at most ``max_lines`` lines are kept (``end`` moves up).
    A range that ends before it starts, or starts past the end, is invalid."""
    if isinstance(start, bool) or isinstance(end, bool):
        raise InvalidQuery("Line numbers are integers.")
    if start > end:
        raise InvalidQuery(f"The range {start}-{end} ends before it starts.")
    if end < 1:
        raise InvalidQuery("Lines start at 1.")
    first = max(start, 1)
    last = min(end, MAX_LINE)
    if line_count is not None:
        last = min(last, line_count)
    if max_lines is not None:
        if max_lines < 1:
            raise InvalidQuery("At least one line must be readable.")
        last = min(last, first + max_lines - 1)
    if first > last:
        raise InvalidQuery(f"Line {first} is past the end of {path} ({line_count} lines).")
    return first, last


def validate_symbol_key(key: str) -> str:
    """A symbol key's shape (PRD §7.3): a known prefix, no control characters, a sane length.
    Whether the snapshot has such a node is the store's answer, not this check's."""
    if not key or len(key) > MAX_KEY_CHARS:
        raise InvalidQuery(f"A symbol key has 1 to {MAX_KEY_CHARS} characters.")
    if _CONTROL.search(key):
        raise InvalidQuery("A symbol key has no control characters.")
    if not key.startswith(_KEY_PREFIXES):
        raise InvalidQuery("A symbol key starts with py:, ts:, file:, dir:, or route:.")
    return key


def validate_blob_oid(oid: str) -> str:
    """A full git blob OID: exactly 40 lowercase hex digits."""
    if not isinstance(oid, str) or _OID.fullmatch(oid) is None:
        raise InvalidQuery("A blob OID is 40 lowercase hexadecimal digits.")
    return oid


def validate_limit(value: int, *, maximum: int, minimum: int = 1, name: str = "limit") -> int:
    """An integer argument within ``minimum`` and ``maximum``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidQuery(f"{name} is an integer.")
    if not minimum <= value <= maximum:
        raise InvalidQuery(f"{name} must be between {minimum} and {maximum}.")
    return value


def validate_text(value: str, *, max_chars: int, name: str = "text") -> str:
    """Free text from outside: a string, not blank, within ``max_chars``, without NUL."""
    if not isinstance(value, str):
        raise InvalidQuery(f"{name} is text.")
    stripped = value.strip()
    if not stripped:
        raise InvalidQuery(f"{name} is empty.")
    if len(stripped) > max_chars:
        raise InvalidQuery(f"{name} is longer than {max_chars} characters.")
    if "\x00" in stripped:
        raise InvalidQuery(f"{name} contains a NUL character.")
    return stripped
