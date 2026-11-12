"""Diff targets (IMP-5): a unified diff → changed line positions → changed symbols.

The snapshot is the code the diff applies to (``side="old"``, the default): removed and changed
lines are positions on the diff's old side, and an addition is anchored where it is inserted.
For a snapshot that already contains the change, ``side="new"`` reads the new side instead
(added lines are the changed positions; removals are anchored where they were).

Each position maps to the innermost class, function, method, or test containing it (``locate``),
else to the file (module-level code). An insertion maps to the symbol containing the line before
it, or, when that line is module-level, the line after it (so a decorator or a first statement
lands on its symbol). New files, deleted files (on the matching side), binary patches, and paths
outside the snapshot contribute nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from repoatlas.graph.model import Node, NodeKind
from repoatlas.store.errors import InvalidQuery, PathNotInSnapshot

Side = Literal["old", "new"]
_HUNK = re.compile(r"^@@ -(?P<os>\d+)(?:,(?P<oc>\d+))? \+(?P<ns>\d+)(?:,(?P<nc>\d+))? @@")
_NULL = "/dev/null"


class Locator(Protocol):
    def locate(self, snapshot_id: int, path: str, line: int) -> Node: ...


@dataclass
class FileChange:
    """One file's changes, as positions on both sides of the diff."""

    old_path: str | None
    new_path: str | None
    old_changed: set[int] = field(default_factory=set)  # removed lines (old numbering)
    old_inserts: set[int] = field(default_factory=set)  # additions go before this old line
    new_changed: set[int] = field(default_factory=set)  # added lines (new numbering)
    new_inserts: set[int] = field(default_factory=set)  # removals were before this new line
    binary: bool = False

    def path(self, side: Side) -> str | None:
        return self.old_path if side == "old" else self.new_path

    def positions(self, side: Side) -> tuple[set[int], set[int]]:
        """(changed lines, insertion points) on one side."""
        if side == "old":
            return self.old_changed, self.old_inserts
        return self.new_changed, self.new_inserts


def parse_unified_diff(text: str) -> list[FileChange]:
    """Files and changed positions of a unified diff (``git diff`` or ``diff -u`` output)."""
    files: list[FileChange] = []
    current: FileChange | None = None
    old_no = new_no = old_left = new_left = 0
    pending_old: str | None = None
    saw_old = False
    for line in text.splitlines():  # splitlines also drops a CR before LF
        if current is not None and (old_left > 0 or new_left > 0):
            tag = line[:1]
            if tag in (" ", ""):  # some tools strip the space of an empty context line
                old_no, new_no = old_no + 1, new_no + 1
                old_left, new_left = old_left - 1, new_left - 1
                continue
            if tag == "-":
                current.old_changed.add(old_no)
                current.new_inserts.add(new_no)
                old_no, old_left = old_no + 1, old_left - 1
                continue
            if tag == "+":
                current.new_changed.add(new_no)
                current.old_inserts.add(old_no)
                new_no, new_left = new_no + 1, new_left - 1
                continue
            if tag == "\\":  # "\ No newline at end of file"
                continue
            old_left = new_left = 0  # a truncated hunk: read the line as a header
        if line.startswith("diff --git "):
            current, pending_old, saw_old = None, None, False
        elif line.startswith("--- "):
            pending_old, saw_old = _path(line[4:]), True
        elif line.startswith("+++ ") and saw_old:
            current = FileChange(old_path=pending_old, new_path=_path(line[4:]))
            files.append(current)
            pending_old, saw_old = None, False
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            files.append(FileChange(old_path=None, new_path=None, binary=True))
            current = None
        elif (match := _HUNK.match(line)) is not None and current is not None:
            old_left = int(match["oc"]) if match["oc"] is not None else 1
            new_left = int(match["nc"]) if match["nc"] is not None else 1
            # an empty side is numbered from 0; positions on it start at line 1
            old_no, new_no = max(int(match["os"]), 1), max(int(match["ns"]), 1)
    return files


def _path(value: str) -> str | None:
    """A ``---``/``+++`` path without its timestamp, quotes, or ``a/``/``b/`` prefix."""
    name = value.split("\t", 1)[0].strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1]
    if name == _NULL:
        return None
    if name.startswith(("a/", "b/")):
        name = name[2:]
    return name or None


def changed_symbols_in(
    locator: Locator, snapshot_id: int, unified_diff: str, *, side: Side = "old"
) -> list[str]:
    """The symbol keys a diff changes, in key order (see the module docstring)."""
    keys: set[str] = set()
    for change in parse_unified_diff(unified_diff):
        path = change.path(side)
        if change.binary or path is None:
            continue
        changed, inserts = change.positions(side)
        try:
            for line in sorted(changed):
                node = _locate(locator, snapshot_id, path, line)
                if node is not None:
                    keys.add(node.key)
            for line in sorted(inserts - changed):
                node = _anchor(locator, snapshot_id, path, line)
                if node is not None:
                    keys.add(node.key)
        except PathNotInSnapshot:
            continue
    return sorted(keys)


def _anchor(locator: Locator, snapshot_id: int, path: str, line: int) -> Node | None:
    before = _locate(locator, snapshot_id, path, line - 1) if line > 1 else None
    if before is not None and before.kind != NodeKind.FILE:
        return before
    after = _locate(locator, snapshot_id, path, line)
    if after is not None and after.kind != NodeKind.FILE:
        return after
    return before or after


def _locate(locator: Locator, snapshot_id: int, path: str, line: int) -> Node | None:
    try:
        return locator.locate(snapshot_id, path, line)
    except InvalidQuery:  # past the end of the file
        return None


def changed_symbols(
    snapshot_id: int,
    unified_diff: str,
    *,
    locator: Locator | None = None,
    side: Side = "old",
) -> list[str]:
    """The symbol keys a diff changes in a stored snapshot (a ``GraphStore`` by default)."""
    if locator is None:
        from repoatlas.config import get_settings
        from repoatlas.store.graphstore import GraphStore

        locator = GraphStore.from_settings(get_settings())
    return changed_symbols_in(locator, snapshot_id, unified_diff, side=side)


def touched_paths(changes: Iterable[FileChange], side: Side = "old") -> list[str]:
    return sorted({p for c in changes if (p := c.path(side)) is not None and not c.binary})
