"""Dependencies the routes share: settings, the engine, the graph store, ready snapshots."""

from __future__ import annotations

from typing import Annotated, cast
from urllib.parse import unquote

from fastapi import Depends, Request
from sqlalchemy import Engine

from repoatlas.api.errors import ApiError, ErrorCode
from repoatlas.api.schemas import MAX_KEY_CHARS, SnapshotIdPath, public_error
from repoatlas.config import Settings
from repoatlas.jobs.build import BUILD_JOB
from repoatlas.jobs.queue import latest_job
from repoatlas.store.graphstore import GraphStore


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def get_store(request: Request) -> GraphStore:
    return cast(GraphStore, request.app.state.store)


SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
StoreDep = Annotated[GraphStore, Depends(get_store)]


def ready_snapshot(snapshot_id: SnapshotIdPath, request: Request) -> int:
    """The snapshot ID of a read, once the snapshot is ready: 404 when it doesn't exist, 409
    ``snapshot_not_ready`` (with its status and build job) while it is not ready. A ready
    snapshot never changes, so each is checked once per app."""
    ready = cast(set[int], request.app.state.ready_snapshots)
    if snapshot_id in ready:
        return snapshot_id
    info = get_store(request).snapshot(snapshot_id)
    if info.status != "ready":
        job = latest_job(snapshot_id, kind=BUILD_JOB, engine=get_engine(request))
        raise ApiError(
            409,
            ErrorCode.SNAPSHOT_NOT_READY,
            f"Snapshot {snapshot_id} is {info.status}; it can be read once it is ready.",
            details={
                "snapshot_id": snapshot_id,
                "status": info.status,
                "job_id": None if job is None else job.id,
                "job_status": None if job is None else job.status,
                "error": public_error(info.error),
            },
        )
    ready.add(snapshot_id)
    return snapshot_id


ReadySnapshot = Annotated[int, Depends(ready_snapshot)]


def decode_key(raw: str) -> str:
    """A symbol key from its path segment, decoded exactly once."""
    try:
        key = unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ApiError(
            422, ErrorCode.INVALID_REQUEST, "The symbol key is not valid UTF-8 once decoded."
        ) from None
    if not key or len(key) > MAX_KEY_CHARS or "\x00" in key:
        raise ApiError(
            422,
            ErrorCode.INVALID_REQUEST,
            f"A symbol key has 1 to {MAX_KEY_CHARS} characters and no NUL characters.",
        )
    return key
