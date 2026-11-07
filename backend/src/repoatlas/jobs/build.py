"""The ``build`` job: a worker runs the registered stages for one snapshot (API-3, API-4).

A build request (``POST /repos``) resolves the repository, pins the commit, computes the config
fingerprint, creates the snapshot as ``pending``, and enqueues a build job whose payload says
exactly what to build. The worker first checks that its own pipeline has the same fingerprint:
a worker running other code or settings would label its output wrongly, so the job fails at
once. It then marks the snapshot ``building`` and runs the stages, reporting the current stage
and progress on the job and stopping between stages when asked to.

Stages keep checkpoints in the build's work directory, so a retried attempt resumes where the
last one stopped. The directory is removed when the build succeeds or fails for good; a build
that fails for good marks its snapshot ``failed`` with the job's error.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Connection, Engine, text

from repoatlas.config import Settings
from repoatlas.ingest.errors import (
    InvalidRepoUrl,
    RepoEmpty,
    RepoNotFound,
    RepoPrivate,
    RepoTooLarge,
)
from repoatlas.ingest.fetch import remove_tree
from repoatlas.ingest.github import GitHubClient, RepoMeta, RepoRef
from repoatlas.jobs import queue
from repoatlas.jobs.queue import Job
from repoatlas.jobs.worker import JobContext, JobHandler, register_job_handler
from repoatlas.logging import get_logger
from repoatlas.pipeline.build import Target, build_dir, open_build_dir, resolve_target
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import (
    config_fingerprint,
    fingerprint_parts,
    registered_stages,
    run_stages,
)
from repoatlas.store.loader import begin_snapshot, mark_failed, mark_pending, request_snapshot
from repoatlas.store.models import SnapshotId, SnapshotStatus

BUILD_JOB = "build"

log = get_logger(__name__)


class BuildPayload(BaseModel):
    """What a build job builds: the snapshot, and the repository as GitHub described it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: int
    url: str
    owner: str
    name: str
    default_branch: str
    commit: str
    fingerprint: str
    size_kb: int = 0
    html_url: str = ""
    archived: bool = False
    fork: bool = False

    @classmethod
    def for_target(cls, snapshot_id: int, url: str, target: Target) -> BuildPayload:
        meta = target.repo
        return cls(
            snapshot_id=snapshot_id,
            url=url,
            owner=meta.ref.owner,
            name=meta.ref.name,
            default_branch=meta.default_branch,
            commit=target.commit,
            fingerprint=target.fingerprint,
            size_kb=meta.size_kb,
            html_url=meta.html_url,
            archived=meta.archived,
            fork=meta.fork,
        )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def repo_meta(self) -> RepoMeta:
        return RepoMeta(
            ref=RepoRef(owner=self.owner, name=self.name),
            default_branch=self.default_branch,
            private=False,
            size_kb=self.size_kb,
            html_url=self.html_url,
            archived=self.archived,
            fork=self.fork,
        )


class PipelineMismatch(Exception):
    """The worker's pipeline (its code or settings) is not the one the build was requested for,
    or the snapshot no longer matches the request. Never retried."""


def dedupe_key(snapshot_id: int) -> str:
    """At most one queued or running build per snapshot."""
    return f"{BUILD_JOB}:snapshot:{snapshot_id}"


def enqueue_build(payload: BuildPayload, settings: Settings, *, connection: Connection) -> Job:
    """Enqueue the build of ``payload.snapshot_id`` inside the caller's transaction (or return
    the build already queued or running for it)."""
    return queue.enqueue(
        BUILD_JOB,
        payload.model_dump(mode="json"),
        snapshot_id=payload.snapshot_id,
        dedupe_key=dedupe_key(payload.snapshot_id),
        max_attempts=settings.job_max_attempts,
        connection=connection,
    )


@dataclass(frozen=True)
class BuildRequest:
    """The answer to a build request. ``reused``: no new build started, because the snapshot
    was ready or its build was already queued or running. ``job_id`` is that build (for a
    ready snapshot, the job that built it, or ``None`` if it was built outside the queue)."""

    snapshot_id: int
    job_id: int | None
    reused: bool
    status: SnapshotStatus


def request_build(
    url: str, settings: Settings, *, engine: Engine, github: GitHubClient | None = None
) -> BuildRequest:
    """Resolve ``url`` (GitHub checks, pinned head, fingerprint), then reuse the snapshot of
    (repo, commit, fingerprint) or queue its build. The snapshot row is locked while deciding,
    so concurrent requests for one snapshot queue one build between them."""
    target = resolve_target(url, settings, github=github)
    canonical = f"https://github.com/{target.repo.ref.full_name}"
    with engine.begin() as connection:
        ref = request_snapshot(
            target.repo, target.commit, target.fingerprint, connection=connection
        )
        if ref.status == "ready":
            built_by = queue.latest_job(ref.id, kind=BUILD_JOB, connection=connection)
            return BuildRequest(ref.id, built_by.id if built_by else None, True, "ready")
        active = queue.active_job(dedupe_key(ref.id), connection=connection)
        if active is not None:
            return BuildRequest(ref.id, active.id, True, ref.status)
        mark_pending(ref.id, connection=connection)
        payload = BuildPayload.for_target(ref.id, canonical, target)
        job = enqueue_build(payload, settings, connection=connection)
    log.info("build queued", snapshot_id=ref.id, job_id=job.id, repo=target.repo.ref.full_name)
    return BuildRequest(ref.id, job.id, False, "pending")


def run_build_job(ctx: JobContext) -> None:
    payload = BuildPayload.model_validate(ctx.job.payload)
    settings, engine = ctx.settings, ctx.engine
    stages = registered_stages()
    expected = config_fingerprint(settings, stages)
    if payload.fingerprint != expected:
        raise PipelineMismatch(
            f"The build was requested for pipeline {payload.fingerprint[:12]}, but this worker "
            f"runs {expected[:12]}. Run the API and the workers with the same code and settings."
        )
    status = _check_snapshot(engine, payload)
    if status == "ready":  # built meanwhile (by another request or `repoatlas build`)
        remove_tree(build_dir(settings, payload.full_name, payload.commit, payload.fingerprint))
        return
    repo = payload.repo_meta()
    ref = begin_snapshot(repo, payload.commit, payload.fingerprint, engine=engine)  # → building
    if ref.id != payload.snapshot_id:
        raise PipelineMismatch(f"Snapshot {payload.snapshot_id} is now snapshot {ref.id}.")
    build = BuildContext(
        url=payload.url,
        settings=settings,
        engine=engine,
        commit=payload.commit,
        repo=repo,
        fingerprint=payload.fingerprint,
        fingerprint_parts=fingerprint_parts(settings, stages),
        snapshot_id=SnapshotId(payload.snapshot_id),
    )
    build.workdir = open_build_dir(build)
    with build.resources:
        run_stages(build, stages, report=ctx.report, check=ctx.check)
    if build.resumed:
        log.info("build resumed from checkpoints", stages=build.resumed)
    remove_tree(build.workdir)


def _check_snapshot(engine: Engine, payload: BuildPayload) -> str:
    """The snapshot's status, after checking it is still the one the job was created for."""
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT s.status, s.commit_sha, s.config_fingerprint, r.owner, r.name "
                "FROM snapshots s JOIN repos r ON r.id = s.repo_id WHERE s.id = :id"
            ),
            {"id": payload.snapshot_id},
        ).first()
    if row is None:
        raise PipelineMismatch(f"Snapshot {payload.snapshot_id} no longer exists.")
    if (row.commit_sha, row.config_fingerprint) != (payload.commit, payload.fingerprint) or (
        f"{row.owner}/{row.name}".lower() != payload.full_name.lower()
    ):
        raise PipelineMismatch(f"Snapshot {payload.snapshot_id} is not the one this job builds.")
    return str(row.status)


def on_build_failed(job: Job, settings: Settings, engine: Engine) -> None:
    """The build failed for good: record why on the snapshot and drop its work directory."""
    try:
        payload = BuildPayload.model_validate(job.payload)
    except ValidationError:
        payload = None
    snapshot_id = payload.snapshot_id if payload is not None else job.snapshot_id
    if snapshot_id is not None:
        mark_failed(snapshot_id, job.error or "The build failed.", engine=engine)
    if payload is not None:
        remove_tree(build_dir(settings, payload.full_name, payload.commit, payload.fingerprint))


BUILD_HANDLER = JobHandler(
    kind=BUILD_JOB,
    run=run_build_job,
    on_failed=on_build_failed,
    permanent_errors=(
        PipelineMismatch,
        ValidationError,
        InvalidRepoUrl,
        RepoPrivate,
        RepoNotFound,
        RepoEmpty,
        RepoTooLarge,
    ),
)
register_job_handler(BUILD_HANDLER)
