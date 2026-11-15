"""Change detection between two snapshots of one repository (PRF-2).

``diff_files`` compares the files of a previous snapshot with a new inventory, by path and blob
OID, and sorts every path into one of five classes:

- **unchanged**: same path, same blob, same skip state;
- **modified**: same path, another blob (or the file is now skipped, or no longer is);
- **added**: a new path;
- **deleted**: a path that is gone;
- **renamed**: a path that is gone and a new path with the same blob. Symbol keys contain the
  path, so for everything built from a file a rename is a delete and an add; the class exists
  so a build can say what happened.

A file's mode is not compared: nothing built from a file depends on it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text

from repoatlas.ingest.inventory import FileEntry, Inventory


class FileState(BaseModel):
    """What change detection compares about one file."""

    model_config = ConfigDict(frozen=True)

    blob_oid: str
    skip_reason: str | None = None

    @classmethod
    def of(cls, entry: FileEntry) -> FileState:
        return cls(blob_oid=entry.blob_oid, skip_reason=entry.skip_reason)


class Rename(BaseModel):
    model_config = ConfigDict(frozen=True)

    old: str
    new: str


class ChangeSet(BaseModel):
    """Every path of the two snapshots, in exactly one class; each list is sorted."""

    model_config = ConfigDict(frozen=True)

    unchanged: list[str] = []
    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    renamed: list[Rename] = []

    @property
    def is_empty(self) -> bool:
        """Whether nothing changed at all."""
        return not (self.modified or self.added or self.deleted or self.renamed)

    @property
    def arrived(self) -> list[str]:
        """Paths of the new snapshot whose content is not the previous snapshot's content at
        that path: modified and added files, and the new paths of renames."""
        return sorted([*self.modified, *self.added, *(rename.new for rename in self.renamed)])

    @property
    def gone(self) -> list[str]:
        """Paths of the previous snapshot that the new one does not have: deleted files and the
        old paths of renames."""
        return sorted([*self.deleted, *(rename.old for rename in self.renamed)])

    def counts(self) -> dict[str, int]:
        return {
            "unchanged": len(self.unchanged),
            "modified": len(self.modified),
            "added": len(self.added),
            "deleted": len(self.deleted),
            "renamed": len(self.renamed),
        }


def diff_states(previous: Mapping[str, FileState], current: Mapping[str, FileState]) -> ChangeSet:
    """The change set from ``previous`` to ``current`` (path → state)."""
    unchanged: list[str] = []
    modified: list[str] = []
    for path in sorted(previous.keys() & current.keys()):
        (unchanged if previous[path] == current[path] else modified).append(path)

    # A rename pairs a path that is gone with a new path holding the same content. Several
    # files with one content pair up in path order; the rest stay deleted or added.
    gone_by_state: dict[FileState, list[str]] = {}
    for path in sorted(previous.keys() - current.keys()):
        gone_by_state.setdefault(previous[path], []).append(path)
    renamed: list[Rename] = []
    added: list[str] = []
    for path in sorted(current.keys() - previous.keys()):
        candidates = gone_by_state.get(current[path])
        if candidates:
            renamed.append(Rename(old=candidates.pop(0), new=path))
        else:
            added.append(path)
    deleted = sorted(path for paths in gone_by_state.values() for path in paths)
    return ChangeSet(
        unchanged=unchanged,
        modified=modified,
        added=added,
        deleted=deleted,
        renamed=sorted(renamed, key=lambda rename: (rename.old, rename.new)),
    )


def inventory_states(entries: Iterable[FileEntry]) -> dict[str, FileState]:
    return {entry.path: FileState.of(entry) for entry in entries}


def diff_files(previous: Mapping[str, FileState], inventory: Inventory) -> ChangeSet:
    """The change set from a previous snapshot's files to ``inventory``."""
    return diff_states(previous, inventory_states(inventory.files))


def snapshot_files(snapshot_id: int, *, engine: Engine) -> dict[str, FileState]:
    """The files of a stored snapshot, as change detection sees them."""
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT path, blob_oid, skip_reason FROM files WHERE snapshot_id = :s"),
            {"s": snapshot_id},
        ).all()
    return {row.path: FileState(blob_oid=row.blob_oid, skip_reason=row.skip_reason) for row in rows}
