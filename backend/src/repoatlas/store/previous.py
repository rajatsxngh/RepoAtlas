"""Reading the previous build of a repository for an incremental build (PRF-2, decision 0024).

``find_previous`` picks the snapshot a new build can reuse: the newest ready snapshot of the
same repository and config fingerprint. ``load_previous`` turns it into what the graph linker
needs (``repoatlas.graph.incremental.Previous``): its per-file records, the change set's paths,
the names its changed files defined, and a reader for the stored call results of the files
that are kept. A snapshot built before per-file records existed has none and can't be reused
for linking.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import Engine, text

from repoatlas.graph.incremental import FileCalls, FileRecord, Previous
from repoatlas.store import rows
from repoatlas.store.models import SnapshotId


def find_previous(snapshot_id: int, *, engine: Engine) -> SnapshotId | None:
    """The newest ready snapshot of the same repository and config fingerprint as
    ``snapshot_id`` (so: of another commit), or ``None``."""
    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT p.id FROM snapshots s JOIN snapshots p ON p.repo_id = s.repo_id "
                "AND p.config_fingerprint = s.config_fingerprint AND p.id <> s.id "
                "WHERE s.id = :id AND p.status = 'ready' "
                "ORDER BY p.updated_at DESC, p.id DESC LIMIT 1"
            ),
            {"id": snapshot_id},
        ).scalar_one_or_none()
    return None if found is None else SnapshotId(found)


def is_reusable(previous_id: int, snapshot_id: int, *, engine: Engine) -> bool:
    """Whether ``previous_id`` is a ready snapshot of the same repository and config
    fingerprint as ``snapshot_id``."""
    with engine.connect() as connection:
        found = connection.execute(
            text(
                "SELECT 1 FROM snapshots s JOIN snapshots p ON p.repo_id = s.repo_id "
                "AND p.config_fingerprint = s.config_fingerprint AND p.id <> s.id "
                "WHERE s.id = :id AND p.id = :previous AND p.status = 'ready'"
            ),
            {"id": snapshot_id, "previous": previous_id},
        ).first()
    return found is not None


def load_records(snapshot_id: int, *, engine: Engine) -> dict[str, FileRecord]:
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT path, imports_digest, calls FROM file_records WHERE snapshot_id = :s"),
            {"s": snapshot_id},
        ).all()
    return {
        row.path: FileRecord(imports_digest=row.imports_digest, calls=dict(row.calls))
        for row in found
    }


def load_calls(snapshot_id: int, paths: Collection[str], *, engine: Engine) -> dict[str, FileCalls]:
    """The stored call results of ``paths``: ``calls`` edges and unresolved calls whose caller
    is a node of the file."""
    wanted = sorted(paths)
    if not wanted:
        return {}
    params = {"s": snapshot_id, "paths": wanted}
    found: dict[str, FileCalls] = {}
    with engine.connect() as connection:
        for row in connection.execute(
            text(
                f"SELECT s.path AS caller_path, {rows.EDGE_SELECT} FROM {rows.EDGE_FROM} "
                "WHERE e.snapshot_id = :s AND e.kind = 'calls' AND s.path = ANY(:paths)"
            ),
            params,
        ):
            found.setdefault(row.caller_path, FileCalls()).edges.append(rows.edge_from_row(row))
        for row in connection.execute(
            text(
                f"SELECT c.path AS caller_path, {rows.UNRESOLVED_SELECT} "
                f"FROM {rows.UNRESOLVED_FROM} "
                "WHERE u.snapshot_id = :s AND c.path = ANY(:paths)"
            ),
            params,
        ):
            found.setdefault(row.caller_path, FileCalls()).unresolved.append(
                rows.unresolved_from_row(row)
            )
    return found


def old_names(snapshot_id: int, paths: Collection[str], *, engine: Engine) -> set[str]:
    """Names of the symbols the snapshot defines in ``paths``."""
    wanted = sorted(paths)
    if not wanted:
        return set()
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT DISTINCT name FROM nodes WHERE snapshot_id = :s "
                    "AND path = ANY(:paths) AND kind NOT IN ('dir', 'file')"
                ),
                {"s": snapshot_id, "paths": wanted},
            ).scalars()
        )


def load_previous(
    snapshot_id: int,
    *,
    arrived: Collection[str],
    gone: Collection[str],
    modified: Collection[str],
    engine: Engine,
) -> Previous | None:
    """What linking reuses from ``snapshot_id``, given how the new commit differs from it (the
    paths of ``pipeline.changes.ChangeSet``); ``None`` when the snapshot has no per-file
    records."""
    records = load_records(snapshot_id, engine=engine)
    if not records:
        return None
    replaced = [*gone, *modified]
    return Previous(
        records=records,
        arrived=arrived,
        gone=gone,
        old_names=old_names(snapshot_id, replaced, engine=engine),
        load_calls=lambda paths: load_calls(snapshot_id, paths, engine=engine),
    )
