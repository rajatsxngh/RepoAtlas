"""Request and response models of the HTTP API (PRD §8). Graph results reuse the store's own
models (``repoatlas.store.results``), so nodes and edges look the same everywhere."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field

from repoatlas.jobs.models import JobStatus
from repoatlas.jobs.queue import Job
from repoatlas.store.models import SnapshotStatus

MAX_ID = 2**63 - 1  # Postgres bigint
MAX_URL_CHARS = 512

SnapshotIdPath = Annotated[int, Path(ge=1, le=MAX_ID, description="Snapshot ID.")]
JobIdPath = Annotated[int, Path(ge=1, le=MAX_ID, description="Job ID.")]

# Recorded failures of these types are written for people (see repoatlas.ingest.errors).
SAFE_ERROR_TYPES = frozenset(
    {
        "InvalidRepoUrl",
        "RepoNotFound",
        "RepoPrivate",
        "RepoEmpty",
        "RepoTooLarge",
        "RateLimited",
        "GitHubError",
        "FetchError",
        "PipelineMismatch",
    }
)
_TYPED_ERROR = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*): ")


def public_error(error: str | None) -> str | None:
    """A recorded failure as clients see it. Ingestion errors and the queue's own sentences
    are shown as they are; any other exception's message could carry internals (SQL,
    parameters, paths), so only its type is shown."""
    if error is None:
        return None
    match = _TYPED_ERROR.match(error)
    if match is None or match.group(1) in SAFE_ERROR_TYPES:
        return error
    return (
        f"{match.group(1)}: the build failed because of an internal error; "
        "the worker's log has the details."
    )


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HealthStatus(_Schema):
    """Liveness: the process answers requests."""

    status: Literal["ok"] = "ok"
    version: str


class ReadyStatus(_Schema):
    """Readiness: the database is reachable and migrated to the revision this code expects."""

    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unreachable", "not_migrated"]
    revision: str | None
    expected_revision: str | None


class BuildRequestBody(_Schema):
    url: str = Field(
        min_length=1,
        max_length=MAX_URL_CHARS,
        description="A public repository: https://github.com/<owner>/<repo>.",
        examples=["https://github.com/pallets/flask"],
    )


class BuildAccepted(_Schema):
    """A build request's answer. Follow ``job_id`` (or the snapshot) until ``ready``."""

    snapshot_id: int
    job_id: int | None = Field(
        description="The build job: queued or running, or for a ready snapshot the job that "
        "built it (null when it was built outside the queue, with `repoatlas build`)."
    )
    reused: bool = Field(
        description="True when no new build started: the snapshot was ready, or its build was "
        "already queued or running."
    )
    status: SnapshotStatus = Field(description="The snapshot's status.")


class JobView(_Schema):
    """A background job and how far it has got."""

    id: int
    kind: str
    snapshot_id: int | None
    status: JobStatus
    stage: str | None = Field(description="The stage the current (or last) attempt reached.")
    progress: float = Field(ge=0, le=1, description="Share of the build done, 0 to 1.")
    attempts: int = Field(description="Attempts started so far.")
    max_attempts: int
    error: str | None = Field(description="Why the last attempt failed, if it did.")
    run_after: datetime = Field(description="When a queued job becomes due (retries wait).")
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, job: Job) -> JobView:
        return cls(
            id=job.id,
            kind=job.kind,
            snapshot_id=job.snapshot_id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            error=public_error(job.error),
            run_after=job.run_after,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
