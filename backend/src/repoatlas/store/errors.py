"""Errors raised by the graph store; ``code`` is a stable machine-readable name."""

from __future__ import annotations


class StoreError(Exception):
    """Base class for every store failure that a caller can act on."""

    code = "store_error"


class SnapshotNotFound(StoreError):
    code = "snapshot_not_found"


class SnapshotNotReady(StoreError):
    """The snapshot exists but is still being built, or its build failed."""

    code = "snapshot_not_ready"

    def __init__(self, message: str, status: str) -> None:
        super().__init__(message)
        self.status = status


class NodeNotFound(StoreError):
    code = "node_not_found"


class PathNotInSnapshot(StoreError):
    """A code read or locate named a file the snapshot does not have."""

    code = "path_not_in_snapshot"


class TextNotStored(StoreError):
    """The file is in the snapshot but was skipped (binary, too large, …), so it has no text."""

    code = "text_not_stored"


class InvalidQuery(StoreError):
    """The request itself is invalid: an unknown level, a bad line range, a depth out of range."""

    code = "invalid_query"
