"""Write a snapshot into the store, and read whole graphs back (STO-2, STO-3, STO-7).

``save_snapshot`` runs in three short steps:

1. Its own transaction upserts the repo and gets or creates the snapshot row (marked
   ``building`` unless it is already ``ready``), so a build is visible while it runs.
2. One transaction locks that row, bulk-loads blobs, files, nodes, edges, unresolved calls, and
   owners with ``COPY``, and sets the status to ``ready``. Readers see either nothing or the
   whole snapshot, and ``ready`` becomes visible only when everything else commits.
3. If step 2 fails, a separate transaction marks the snapshot ``failed`` with the error.

Saving is idempotent: a (repo, commit, fingerprint) that is already ``ready`` is returned
without writing anything. Blobs are shared by OID across snapshots; text a snapshot needs that
the store already has is neither read nor sent again.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import psycopg
from pydantic import JsonValue
from sqlalchemy import Connection, Engine, text

from repoatlas.canonical import canonical_json
from repoatlas.db.engine import get_engine
from repoatlas.graph.model import CodeGraph, GraphStats, NodeKind, Owner
from repoatlas.ingest.github import RepoMeta
from repoatlas.ingest.inventory import Inventory
from repoatlas.ingest.source import blob_oid
from repoatlas.logging import get_logger
from repoatlas.store import rows
from repoatlas.store.blobs import decode_blob
from repoatlas.store.errors import SnapshotNotFound, SnapshotNotReady
from repoatlas.store.models import DEFAULT_HOST, SnapshotId, SnapshotStatus

MAX_ERROR_CHARS = 2_000
SNAPSHOT_TABLES = ("owners", "unresolved_calls", "edges", "nodes", "files")  # delete order

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    id: SnapshotId
    status: SnapshotStatus


def save_snapshot(
    repo_meta: RepoMeta,
    sha: str,
    fingerprint: str,
    inventory: Inventory,
    blobs: Mapping[str, bytes],
    graph: CodeGraph,
    *,
    engine: Engine | None = None,
    extra_stats: Mapping[str, JsonValue] | None = None,
) -> SnapshotId:
    """Store one snapshot and return its ID (see the module docstring for the steps).

    ``blobs`` maps blob OIDs to raw bytes and must cover every indexed (not skipped) file the
    store does not already have; it may be lazy, since only missing OIDs are looked up.
    ``extra_stats`` are merged into the snapshot's ``stats`` (for example step timings); the keys
    the store computes itself (``graph``, ``graph_digest``, ``inventory``, ``blobs``, ``rows``,
    ``store_seconds``) always win.
    """
    engine = engine or get_engine()
    ref = begin_snapshot(repo_meta, sha, fingerprint, engine=engine)
    if ref.status == "ready":
        return ref.id
    started = time.perf_counter()
    try:
        with engine.begin() as connection:
            status = connection.execute(
                text("SELECT status FROM snapshots WHERE id = :id FOR UPDATE"), {"id": ref.id}
            ).scalar_one_or_none()
            if status is None:
                raise SnapshotNotFound(f"Snapshot {ref.id} was deleted while it was being saved.")
            if status == "ready":  # another process finished it while we waited for the lock
                return ref.id
            for table in SNAPSHOT_TABLES:
                connection.execute(
                    text(f"DELETE FROM {table} WHERE snapshot_id = :id"), {"id": ref.id}
                )
            counts = _write(connection, ref.id, inventory, blobs, graph)
            stats: dict[str, JsonValue] = {
                **(extra_stats or {}),  # never overrides what the store computes below
                "graph": graph.stats.model_dump(mode="json"),
                "graph_digest": graph.digest(),
                "inventory": inventory.stats.model_dump(mode="json"),
                **counts,
                "store_seconds": round(time.perf_counter() - started, 3),
            }
            connection.execute(
                text(
                    "UPDATE snapshots SET status = 'ready', stats = CAST(:stats AS jsonb), "
                    "error = NULL, updated_at = now() WHERE id = :id"
                ),
                {"id": ref.id, "stats": canonical_json(stats)},
            )
    except Exception as error:
        mark_failed(ref.id, error, engine=engine)
        raise
    log.info("snapshot saved", snapshot_id=ref.id, repo=repo_meta.ref.full_name, commit=sha)
    return ref.id


def begin_snapshot(
    repo_meta: RepoMeta, sha: str, fingerprint: str, *, engine: Engine | None = None
) -> SnapshotRef:
    """Upsert the repo and get or create its snapshot for (commit, fingerprint). A snapshot that
    is not ``ready`` is (re)marked ``building`` with its error cleared; a ``ready`` one is left
    untouched."""
    engine = engine or get_engine()
    repo = {
        "host": DEFAULT_HOST,
        "owner": repo_meta.ref.owner,
        "name": repo_meta.ref.name,
        "branch": repo_meta.default_branch,
    }
    with engine.begin() as connection:
        repo_id = connection.execute(
            text(
                "INSERT INTO repos (host, owner, name, default_branch) "
                "VALUES (:host, :owner, :name, :branch) "
                "ON CONFLICT (host, lower(owner), lower(name)) DO UPDATE SET "
                "owner = EXCLUDED.owner, name = EXCLUDED.name, "
                "default_branch = EXCLUDED.default_branch "
                "WHERE (repos.owner, repos.name, repos.default_branch) "
                "IS DISTINCT FROM (EXCLUDED.owner, EXCLUDED.name, EXCLUDED.default_branch) "
                "RETURNING id"
            ),
            repo,
        ).scalar_one_or_none()
        if repo_id is None:  # already there, unchanged
            repo_id = connection.execute(
                text(
                    "SELECT id FROM repos WHERE host = :host AND lower(owner) = lower(:owner) "
                    "AND lower(name) = lower(:name)"
                ),
                repo,
            ).scalar_one()
        identity = {"repo": repo_id, "sha": sha, "fp": fingerprint}
        select = text(
            "SELECT id, status FROM snapshots "
            "WHERE repo_id = :repo AND commit_sha = :sha AND config_fingerprint = :fp"
        )
        row = connection.execute(select, identity).first()
        if row is None:
            row = (
                connection.execute(
                    text(
                        "INSERT INTO snapshots (repo_id, commit_sha, config_fingerprint, status) "
                        "VALUES (:repo, :sha, :fp, 'building') "
                        "ON CONFLICT (repo_id, commit_sha, config_fingerprint) DO NOTHING "
                        "RETURNING id, status"
                    ),
                    identity,
                ).first()
                or connection.execute(select, identity).one()
            )
        elif row.status != "ready":
            row = (
                connection.execute(
                    text(
                        "UPDATE snapshots SET status = 'building', error = NULL, "
                        "updated_at = now() WHERE id = :id AND status <> 'ready' "
                        "RETURNING id, status"
                    ),
                    {"id": row.id},
                ).first()
                or connection.execute(select, identity).one()
            )
    return SnapshotRef(id=SnapshotId(row.id), status=cast(SnapshotStatus, row.status))


def find_snapshot(
    owner: str, name: str, sha: str, fingerprint: str, *, engine: Engine | None = None
) -> SnapshotRef | None:
    """The snapshot of ``owner/name`` (case-insensitive) at (commit, fingerprint), if any."""
    engine = engine or get_engine()
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT s.id, s.status FROM snapshots s JOIN repos r ON r.id = s.repo_id "
                "WHERE r.host = :host AND lower(r.owner) = lower(:owner) "
                "AND lower(r.name) = lower(:name) AND s.commit_sha = :sha "
                "AND s.config_fingerprint = :fp"
            ),
            {"host": DEFAULT_HOST, "owner": owner, "name": name, "sha": sha, "fp": fingerprint},
        ).first()
    if row is None:
        return None
    return SnapshotRef(id=SnapshotId(row.id), status=cast(SnapshotStatus, row.status))


def mark_failed(
    snapshot_id: int, error: BaseException | str, *, engine: Engine | None = None
) -> None:
    """Record a failed build (never downgrades a ``ready`` snapshot)."""
    engine = engine or get_engine()
    message = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    if len(message) > MAX_ERROR_CHARS:
        message = message[: MAX_ERROR_CHARS - 1] + "…"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE snapshots SET status = 'failed', error = :error, updated_at = now() "
                    "WHERE id = :id AND status <> 'ready'"
                ),
                {"id": snapshot_id, "error": message},
            )
    except Exception:  # the caller re-raises the original error; don't hide it behind this one
        log.exception("could not mark the snapshot failed", snapshot_id=snapshot_id)


def load_graph(snapshot_id: int, *, engine: Engine | None = None) -> CodeGraph:
    """Read a ready snapshot's whole graph back from the store."""
    engine = engine or get_engine()
    with engine.connect() as connection:
        stats = _ready_stats(connection, snapshot_id)
        params = {"id": snapshot_id}
        nodes = [
            rows.node_from_row(row)
            for row in connection.execute(
                text(f"SELECT {rows.NODE_SELECT} FROM {rows.NODE_FROM} WHERE n.snapshot_id = :id"),
                params,
            )
        ]
        edges = [
            rows.edge_from_row(row)
            for row in connection.execute(
                text(f"SELECT {rows.EDGE_SELECT} FROM {rows.EDGE_FROM} WHERE e.snapshot_id = :id"),
                params,
            )
        ]
        unresolved = [
            rows.unresolved_from_row(row)
            for row in connection.execute(
                text(
                    f"SELECT {rows.UNRESOLVED_SELECT} FROM {rows.UNRESOLVED_FROM} "
                    "WHERE u.snapshot_id = :id"
                ),
                params,
            )
        ]
        owners: dict[str, list[Owner]] = {}
        for row in connection.execute(
            text(
                f"SELECT f.path, {rows.OWNER_SELECT} FROM owners o "
                "JOIN files f ON f.id = o.file_id WHERE o.snapshot_id = :id"
            ),
            params,
        ):
            owners.setdefault(row.path, []).append(rows.owner_from_row(row))
    return CodeGraph(
        nodes={node.key: node for node in nodes},
        edges=edges,
        unresolved=unresolved,
        owners=owners,
        stats=GraphStats.model_validate(stats.get("graph", {})),
    )


def _ready_stats(connection: Connection, snapshot_id: int) -> dict[str, Any]:
    row = connection.execute(
        text("SELECT status, stats FROM snapshots WHERE id = :id"), {"id": snapshot_id}
    ).first()
    if row is None:
        raise SnapshotNotFound(f"Snapshot {snapshot_id} does not exist.")
    if row.status != "ready":
        raise SnapshotNotReady(f"Snapshot {snapshot_id} is {row.status}.", status=row.status)
    return cast(dict[str, Any], row.stats)


def _write(
    connection: Connection,
    snapshot_id: int,
    inventory: Inventory,
    blobs: Mapping[str, bytes],
    graph: CodeGraph,
) -> dict[str, JsonValue]:
    """Write every row of one snapshot inside the caller's transaction; return row counts."""
    raw = cast(psycopg.Connection[Any], connection.connection.driver_connection)
    with raw.cursor() as cursor:
        blob_counts = _write_blobs(connection, cursor, inventory, blobs)

        file_ids = dict(
            zip(
                (entry.path for entry in inventory.files),
                _allocate_ids(connection, "files", len(inventory.files)),
                strict=True,
            )
        )
        _copy(
            cursor,
            "files",
            rows.FILE_COLUMNS,
            (
                rows.file_row(entry, file_id=file_ids[entry.path], snapshot_id=snapshot_id)
                for entry in inventory.files
            ),
        )

        node_ids = dict(
            zip(graph.nodes, _allocate_ids(connection, "nodes", len(graph.nodes)), strict=True)
        )
        node_rows = []
        for node in graph.nodes.values():
            file_id = None
            if node.kind != NodeKind.DIR:
                file_id = _lookup(file_ids, node.path, f"node {node.key}'s file")
            parent_id = None
            if node.parent_key is not None:
                parent_id = _lookup(node_ids, node.parent_key, f"node {node.key}'s parent")
            node_rows.append(
                rows.node_row(
                    node,
                    node_id=node_ids[node.key],
                    snapshot_id=snapshot_id,
                    file_id=file_id,
                    parent_id=parent_id,
                )
            )
        _copy(cursor, "nodes", rows.NODE_COLUMNS, node_rows)

        _copy(
            cursor,
            "edges",
            rows.EDGE_COLUMNS,
            (
                rows.edge_row(
                    edge,
                    snapshot_id=snapshot_id,
                    src_id=_lookup(node_ids, edge.src, "an edge's source"),
                    dst_id=_lookup(node_ids, edge.dst, "an edge's destination"),
                )
                for edge in graph.edges
            ),
        )
        _copy(
            cursor,
            "unresolved_calls",
            rows.UNRESOLVED_COLUMNS,
            (
                rows.unresolved_row(
                    call,
                    snapshot_id=snapshot_id,
                    caller_id=_lookup(node_ids, call.caller_key, "an unresolved call's caller"),
                )
                for call in graph.unresolved
            ),
        )
        _copy(
            cursor,
            "owners",
            rows.OWNER_COLUMNS,
            (
                rows.owner_row(
                    owner,
                    snapshot_id=snapshot_id,
                    file_id=_lookup(file_ids, path, "an owner's file"),
                )
                for path, owners in graph.owners.items()
                for owner in owners
            ),
        )
    return {
        "blobs": blob_counts,
        "rows": {
            "files": len(inventory.files),
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "unresolved_calls": len(graph.unresolved),
            "owners": sum(len(owners) for owners in graph.owners.values()),
        },
    }


def _write_blobs(
    connection: Connection,
    cursor: psycopg.Cursor[Any],
    inventory: Inventory,
    blobs: Mapping[str, bytes],
) -> dict[str, JsonValue]:
    """Store the text of every indexed file the store doesn't have yet.

    New blobs go through a temporary table and ``INSERT … ON CONFLICT DO NOTHING``, so two
    builds that add the same blob at the same time both succeed.
    """
    needed = sorted({entry.blob_oid for entry in inventory.indexed})
    present = set(
        connection.execute(
            text("SELECT oid FROM blobs WHERE oid = ANY(:oids)"), {"oids": needed}
        ).scalars()
    )
    missing = [oid for oid in needed if oid not in present]
    stored_bytes = lossy = 0
    if missing:
        connection.execute(
            text(
                "CREATE TEMPORARY TABLE blobs_incoming "
                "(LIKE blobs INCLUDING DEFAULTS) ON COMMIT DROP"
            )
        )

        def incoming() -> Iterable[tuple[object, ...]]:
            nonlocal stored_bytes, lossy
            for oid in missing:
                if oid not in blobs:
                    raise ValueError(f"No bytes were given for blob {oid}.")
                data = blobs[oid]
                if blob_oid(data) != oid:
                    raise ValueError(f"The bytes given for blob {oid} have a different OID.")
                decoded = decode_blob(data)
                stored_bytes += len(data)
                lossy += decoded.lossy
                yield (oid, len(data), decoded.line_count, decoded.text, decoded.lossy)

        _copy(cursor, "blobs_incoming", rows.BLOB_COLUMNS, incoming())
        connection.execute(
            text(
                "INSERT INTO blobs SELECT * FROM blobs_incoming ORDER BY oid "
                "ON CONFLICT (oid) DO NOTHING"
            )
        )
    return {
        "needed": len(needed),
        "stored": len(missing),
        "shared": len(present),
        "stored_bytes": stored_bytes,
        "lossy": lossy,
    }


def _allocate_ids(connection: Connection, table: str, count: int) -> list[int]:
    """Reserve ``count`` IDs from ``table``'s identity sequence (rows reference each other by ID,
    so IDs must be known before ``COPY``)."""
    if count == 0:
        return []
    return list(
        connection.execute(
            text(
                f"SELECT nextval(pg_get_serial_sequence('{table}', 'id')) "
                "FROM generate_series(1, :count)"
            ),
            {"count": count},
        ).scalars()
    )


def _copy(
    cursor: psycopg.Cursor[Any],
    table: str,
    columns: Sequence[str],
    values: Iterable[Sequence[object]],
) -> None:
    with cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in values:
            copy.write_row(row)


def _lookup(ids: Mapping[str, int], key: str, what: str) -> int:
    found = ids.get(key)
    if found is None:
        raise ValueError(f"The graph is inconsistent: {what} {key!r} is not in the snapshot.")
    return found
