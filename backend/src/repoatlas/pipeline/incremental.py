"""``build_incremental`` (PRF-2): build a new commit of a repository on top of a snapshot.

Every build reuses the repository's newest ready snapshot by itself (``pipeline.build
.use_previous``); this entry point names the snapshot to reuse. What is reused, and why the
result equals a full build, is in decision 0024:

- parse results of unchanged files (the parse cache; this needs no previous snapshot);
- call resolution of every file no invalidation rule touches (``graph.incremental``);
- chunks of unchanged files, and embeddings of every chunk whose text did not change.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from repoatlas.config import Settings, get_settings
from repoatlas.db.engine import get_engine
from repoatlas.pipeline.build import run_build
from repoatlas.pipeline.stages import config_fingerprint
from repoatlas.store.errors import SnapshotNotFound, SnapshotNotReady
from repoatlas.store.models import DEFAULT_HOST, SnapshotId


def build_incremental(
    previous_snapshot_id: int,
    new_sha: str,
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
) -> SnapshotId:
    """Build commit ``new_sha`` of the repository of ``previous_snapshot_id``, reusing that
    snapshot, and return the new snapshot's ID (the existing one, if that commit is already
    built). The previous snapshot must be ready and built with this pipeline's configuration."""
    settings = settings or get_settings()
    engine = engine or get_engine()
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT s.status, s.config_fingerprint, r.host, r.owner, r.name "
                "FROM snapshots s JOIN repos r ON r.id = s.repo_id WHERE s.id = :id"
            ),
            {"id": previous_snapshot_id},
        ).first()
    if row is None:
        raise SnapshotNotFound(f"Snapshot {previous_snapshot_id} does not exist.")
    if row.status != "ready":
        raise SnapshotNotReady(
            f"Snapshot {previous_snapshot_id} is {row.status}; only a ready snapshot can be "
            "built on.",
            status=row.status,
        )
    if row.config_fingerprint != config_fingerprint(settings):
        raise ValueError(
            f"Snapshot {previous_snapshot_id} was built with another configuration "
            f"({row.config_fingerprint[:12]}); a build can only reuse a snapshot of its own."
        )
    url = f"https://{row.host or DEFAULT_HOST}/{row.owner}/{row.name}"
    result = run_build(url, settings, engine=engine, commit=new_sha, previous=previous_snapshot_id)
    return result.snapshot_id
