"""Request and response models of the HTTP API (PRD §8). Graph results reuse the store's own
models (``repoatlas.store.results``), so nodes and edges look the same everywhere."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Path, Query
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue

from repoatlas.answers.schema import (
    AnswerMetrics,
    AnswerResult,
    Citation,
    Claim,
    RefusalKind,
)
from repoatlas.graph.model import Node, Owner
from repoatlas.jobs.models import JobStatus
from repoatlas.jobs.queue import Job
from repoatlas.llm.types import Usage
from repoatlas.store.models import SnapshotStatus
from repoatlas.store.results import RepoInfo, SnapshotInfo, SymbolMatch

MAX_ID = 2**63 - 1  # Postgres bigint
MAX_LINE = 2**31 - 1  # Postgres integer
MAX_URL_CHARS = 512
MAX_KEY_CHARS = 4_096
MAX_PATH_CHARS = 4_096
MAX_QUERY_CHARS = 200
MAX_SEARCH_LIMIT = 50


def no_nul(value: str) -> str:
    """Postgres text can't hold NUL characters, and no key, path, or name contains one."""
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


def repo_path(value: str) -> str:
    """A repo-relative POSIX path, as in the snapshot's file list: never absolute, and with no
    empty, ``.``, or ``..`` segments (git trees can't hold those either)."""
    no_nul(value)
    if value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("must be a repo-relative path without empty, '.', or '..' segments")
    return value


SnapshotIdPath = Annotated[int, Path(ge=1, le=MAX_ID, description="Snapshot ID.")]
JobIdPath = Annotated[int, Path(ge=1, le=MAX_ID, description="Job ID.")]
KeyPath = Annotated[
    str,
    Path(
        max_length=MAX_KEY_CHARS * 3,
        description="Symbol key, URL-encoded as one path segment (`/` as `%2F`, `%` as `%25`).",
    ),
]
RepoPathQuery = Annotated[
    str,
    Query(min_length=1, max_length=MAX_PATH_CHARS, description="Repo-relative file path."),
    AfterValidator(repo_path),
]
LineQuery = Annotated[int, Query(ge=1, le=MAX_LINE, description="1-based line number.")]


def key_query(description: str, *, alias: str | None = None) -> object:
    return Query(alias=alias, min_length=1, max_length=MAX_KEY_CHARS, description=description)


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
    """Readiness: the database is reachable and migrated to the revision this code expects, or
    ``ahead`` of it (migrated by a newer release; this one runs again after a rollback)."""

    status: Literal["ready", "not_ready"]
    database: Literal["ok", "ahead", "unreachable", "not_migrated"]
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


class SnapshotView(_Schema):
    """A snapshot (any status): the repository, commit, configuration, and build stats."""

    id: int
    repo: RepoInfo
    commit_sha: str
    config_fingerprint: str
    status: SnapshotStatus
    stats: dict[str, JsonValue]
    embedding_model: str | None
    embedding_dim: int | None
    error: str | None = Field(description="Why the build failed, if it did.")
    created_at: datetime
    updated_at: datetime
    job: JobView | None = Field(description="The snapshot's latest build job, if any.")

    @classmethod
    def of(cls, info: SnapshotInfo, job: Job | None) -> SnapshotView:
        return cls(
            id=info.id,
            repo=info.repo,
            commit_sha=info.commit_sha,
            config_fingerprint=info.config_fingerprint,
            status=info.status,
            stats=info.stats,
            embedding_model=info.embedding_model,
            embedding_dim=info.embedding_dim,
            error=public_error(info.error),
            created_at=info.created_at,
            updated_at=info.updated_at,
            job=None if job is None else JobView.of(job),
        )


class Degree(_Schema):
    """Edges at a node by kind (only kinds with at least one edge)."""

    incoming: dict[str, int]
    outgoing: dict[str, int]


class NodeDetail(_Schema):
    """A node, the owners of its file (most commits first), and its edge counts."""

    node: Node
    owners: list[Owner]
    degree: Degree


class SearchResults(_Schema):
    """Exact matches first (key, name, or qualified name, any case), then prefixes, then
    trigram matches by similarity."""

    query: str
    results: list[SymbolMatch]


# --- answers ----------------------------------------------------------------------------


class AnswerView(BaseModel):
    """A validated answer: only claims with a valid citation, each citation an evidence
    reference. ``metrics`` were measured before invalid citations were removed."""

    run_id: int | None = None
    answer_markdown: str
    claims: list[Claim]
    citations: list[Citation]
    refused: bool
    refusal_kind: RefusalKind | None
    refusal_reason: str | None
    usage: Usage
    cost_usd: float
    trace_id: uuid.UUID | None
    metrics: AnswerMetrics


def answer_view(result: AnswerResult, *, run_id: int | None = None) -> AnswerView:
    """The public part of a result (the model's draft stays in the database)."""
    return AnswerView.model_validate({**result.model_dump(exclude={"draft"}), "run_id": run_id})


class QaRunView(BaseModel):
    id: int
    snapshot_id: int
    mode: str
    question: str
    created_at: datetime
    answer: AnswerView
