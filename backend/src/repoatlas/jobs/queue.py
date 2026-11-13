"""A Postgres job queue with leases, heartbeats, retries, and lease recovery (API-3).

Workers claim the oldest due job with ``SELECT … FOR UPDATE SKIP LOCKED``, so two workers never
get the same job and neither waits for the other. A claim starts an attempt: the job becomes
``running``, ``attempts`` goes up by one, and the worker holds a lease until ``lease_until``,
which its heartbeats push forward. Every later write names the lease (job, worker, attempt) and
changes nothing once the job has moved on, so a worker that lost its lease can't touch the job
again (``LeaseLost``).

A failed attempt goes back to ``queued`` after a backoff (10 s, 60 s, then 300 s by default)
until ``max_attempts`` attempts have been made; then the job is ``failed``. A running job whose
lease ran out (its worker crashed or was killed) counts as a failed attempt in
``recover_expired``. A worker that stops on purpose ``release``\\ s its job instead, which gives
the attempt back. Every time comes from the database clock, so workers' clocks never matter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import Connection, Engine, Row, text

from repoatlas.canonical import canonical_json
from repoatlas.db.engine import get_engine
from repoatlas.jobs.models import JobStatus

DEFAULT_LEASE_S = 60.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_S: tuple[float, ...] = (10.0, 60.0, 300.0)  # before the 2nd, 3rd, later attempts
MAX_ERROR_CHARS = 2_000
CLAIM_LOCK = 0x7265706F61746C  # advisory lock key for capped claims ("repoatl" in ASCII)
_FENCE = "id = :id AND status = 'running' AND worker_id = :worker AND attempts = :attempt"


class Job(BaseModel):
    """A job as it was when it was read (see ``repoatlas.jobs.models`` for the columns)."""

    model_config = ConfigDict(frozen=True)

    id: int
    kind: str
    payload: dict[str, JsonValue]
    status: JobStatus
    stage: str | None
    progress: float
    attempts: int
    max_attempts: int
    snapshot_id: int | None
    dedupe_key: str | None
    worker_id: str | None
    run_after: datetime
    lease_until: datetime | None
    heartbeat_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def lease(self) -> Lease:
        """The lease of a job this worker just claimed."""
        if self.status != "running" or self.worker_id is None:
            raise LeaseLost(f"Job {self.id} is {self.status}, not running.")
        return Lease(job_id=self.id, worker_id=self.worker_id, attempt=self.attempts)


@dataclass(frozen=True, slots=True)
class Lease:
    """Proof that ``worker_id`` runs attempt ``attempt`` of job ``job_id`` (a fencing token)."""

    job_id: int
    worker_id: str
    attempt: int


class LeaseLost(Exception):
    """The job is no longer this worker's: its lease ran out and the job was requeued (or taken
    by another worker), or it has already finished."""


def backoff_delay(attempt: int, schedule: Sequence[float] = DEFAULT_BACKOFF_S) -> float:
    """Seconds to wait after failed attempt ``attempt`` (1-based); the last delay repeats."""
    if not schedule:
        return 0.0
    return float(schedule[min(max(attempt, 1), len(schedule)) - 1])


def error_text(error: BaseException | str) -> str:
    """How a failure is recorded: ``Type: message``, at most ``MAX_ERROR_CHARS`` characters."""
    message = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    message = message.strip() or "Unknown error."
    return message if len(message) <= MAX_ERROR_CHARS else message[: MAX_ERROR_CHARS - 1] + "…"


def enqueue(
    kind: str,
    payload: Mapping[str, JsonValue] | None = None,
    *,
    snapshot_id: int | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    delay_s: float = 0.0,
    connection: Connection | None = None,
    engine: Engine | None = None,
) -> Job:
    """Add a job, due after ``delay_s``. When a queued or running job already holds
    ``dedupe_key``, that job is returned instead. Pass ``connection`` to enqueue inside the
    caller's transaction (the job then appears only if that transaction commits)."""
    if not kind:
        raise ValueError("A job needs a kind.")
    if max_attempts < 1:
        raise ValueError("A job needs at least one attempt.")
    params = {
        "kind": kind,
        "payload": canonical_json(dict(payload or {})),
        "snapshot_id": snapshot_id,
        "dedupe_key": dedupe_key,
        "max_attempts": max_attempts,
        "delay": max(delay_s, 0.0),
    }
    insert = text(
        "INSERT INTO jobs (kind, payload, snapshot_id, dedupe_key, max_attempts, run_after) "
        "VALUES (:kind, CAST(:payload AS jsonb), :snapshot_id, :dedupe_key, :max_attempts, "
        "now() + make_interval(secs => :delay)) "
        "ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING "
        "RETURNING *"
    )

    def run(conn: Connection) -> Job:
        for _ in range(3):  # the holder of the key may finish between the insert and the select
            row = conn.execute(insert, params).first()
            if row is None:
                row = conn.execute(
                    text(
                        "SELECT * FROM jobs WHERE dedupe_key = :dedupe_key "
                        "AND status IN ('queued', 'running')"
                    ),
                    params,
                ).first()
            if row is not None:
                return _job(row)
        raise RuntimeError(f"Could not enqueue a {kind} job for key {dedupe_key!r}.")

    if connection is not None:
        return run(connection)
    with (engine or get_engine()).begin() as conn:
        return run(conn)


def claim(
    worker_id: str,
    *,
    kinds: Sequence[str] | None = None,
    lease_s: float = DEFAULT_LEASE_S,
    max_running: int | None = None,
    engine: Engine | None = None,
) -> Job | None:
    """Start the next attempt of the oldest due job (of ``kinds``), or return ``None``.

    With ``max_running``, nothing is claimed while that many jobs (of ``kinds``) are running,
    whichever workers run them (DEP-5). Claims that share a cap take turns (a transaction-level
    advisory lock), so two workers can't both take the last free place.
    """
    if kinds is not None and not kinds:
        return None
    if max_running is not None and max_running < 1:
        raise ValueError("max_running must be at least 1 (or None for no cap).")
    kind_filter = "AND kind = ANY(:kinds)" if kinds is not None else ""
    statement = text(
        "WITH next AS (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now() "
        f"{kind_filter} ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "UPDATE jobs SET status = 'running', worker_id = :worker, attempts = jobs.attempts + 1, "
        "stage = NULL, progress = 0, error = NULL, heartbeat_at = now(), "
        "lease_until = now() + make_interval(secs => :lease), "
        "started_at = coalesce(jobs.started_at, now()), updated_at = now() "
        "FROM next WHERE jobs.id = next.id RETURNING jobs.*"
    )
    params: dict[str, Any] = {"worker": worker_id, "lease": lease_s}
    if kinds is not None:
        params["kinds"] = list(kinds)
    with (engine or get_engine()).begin() as connection:
        if max_running is not None:
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_LOCK})
            running = connection.execute(
                text(f"SELECT count(*) FROM jobs WHERE status = 'running' {kind_filter}"), params
            ).scalar_one()
            if running >= max_running:
                return None
        row = connection.execute(statement, params).first()
    return None if row is None else _job(row)


def heartbeat(
    lease: Lease, *, lease_s: float = DEFAULT_LEASE_S, engine: Engine | None = None
) -> datetime:
    """Extend the lease by ``lease_s`` from now; return the new end of the lease."""
    with (engine or get_engine()).begin() as connection:
        until = connection.execute(
            text(
                "UPDATE jobs SET heartbeat_at = now(), "
                f"lease_until = now() + make_interval(secs => :lease) WHERE {_FENCE} "
                "RETURNING lease_until"
            ),
            {**_fence(lease), "lease": lease_s},
        ).scalar_one_or_none()
    if until is None:
        raise LeaseLost(f"Job {lease.job_id} attempt {lease.attempt} lost its lease.")
    return until  # type: ignore[no-any-return]


def report(
    lease: Lease, *, stage: str | None, progress: float, engine: Engine | None = None
) -> None:
    """Record the current stage and overall progress (clamped to 0..1) of the attempt."""
    with (engine or get_engine()).begin() as connection:
        changed = connection.execute(
            text(
                "UPDATE jobs SET stage = :stage, progress = :progress, updated_at = now() "
                f"WHERE {_FENCE}"
            ),
            {**_fence(lease), "stage": stage, "progress": min(max(progress, 0.0), 1.0)},
        ).rowcount
    if not changed:
        raise LeaseLost(f"Job {lease.job_id} attempt {lease.attempt} lost its lease.")


def complete(lease: Lease, *, engine: Engine | None = None) -> Job:
    """Mark the attempt's job ``succeeded``."""
    return _finish(
        lease,
        "status = 'succeeded', progress = 1, error = NULL, lease_until = NULL, "
        "finished_at = now(), updated_at = now()",
        {},
        engine,
    )


def fail(
    lease: Lease,
    error: BaseException | str,
    *,
    retry: bool = True,
    backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
    engine: Engine | None = None,
) -> Job:
    """Record a failed attempt. The job is queued again after a backoff while it has attempts
    left and ``retry`` is set; otherwise it is ``failed`` for good."""
    with (engine or get_engine()).begin() as connection:
        row = connection.execute(
            text(f"SELECT attempts, max_attempts FROM jobs WHERE {_FENCE} FOR UPDATE"),
            _fence(lease),
        ).first()
        if row is None:
            raise LeaseLost(f"Job {lease.job_id} attempt {lease.attempt} lost its lease.")
        params: dict[str, Any] = {**_fence(lease), "error": error_text(error)}
        if retry and row.attempts < row.max_attempts:
            params["delay"] = backoff_delay(row.attempts, backoff_s)
            changes = (
                "status = 'queued', worker_id = NULL, lease_until = NULL, "
                "run_after = now() + make_interval(secs => :delay)"
            )
        else:
            changes = "status = 'failed', lease_until = NULL, finished_at = now()"
        updated = connection.execute(
            text(
                f"UPDATE jobs SET {changes}, error = :error, updated_at = now() "
                f"WHERE {_FENCE} RETURNING *"
            ),
            params,
        ).one()
    return _job(updated)


def release(lease: Lease, *, engine: Engine | None = None) -> Job:
    """Give the job back, due now, without counting the attempt (a worker stopping on purpose).
    Its stage and progress stay, to show where it stopped."""
    return _finish(
        lease,
        "status = 'queued', attempts = greatest(attempts - 1, 0), worker_id = NULL, "
        "lease_until = NULL, run_after = now(), updated_at = now()",
        {},
        engine,
    )


def recover_expired(
    *,
    kinds: Sequence[str] | None = None,
    backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
    engine: Engine | None = None,
) -> list[Job]:
    """Treat every running job (of ``kinds``) whose lease ran out as a failed attempt: queue it
    again after a backoff, or fail it when it has no attempts left. Returns the jobs it
    changed."""
    if kinds is not None and not kinds:
        return []
    schedule = [float(delay) for delay in backoff_s] or [0.0]
    out_of_attempts = "jobs.attempts >= jobs.max_attempts"
    kind_filter = "AND kind = ANY(:kinds)" if kinds is not None else ""
    statement = text(
        "WITH expired AS (SELECT id FROM jobs WHERE status = 'running' AND lease_until < now() "
        f"{kind_filter} ORDER BY id FOR UPDATE SKIP LOCKED) "
        f"UPDATE jobs SET status = CASE WHEN {out_of_attempts} THEN 'failed' ELSE 'queued' END, "
        f"run_after = CASE WHEN {out_of_attempts} THEN jobs.run_after ELSE now() + "
        "make_interval(secs => (CAST(:schedule AS double precision[]))"
        "[least(greatest(jobs.attempts, 1), :steps)]) END, "
        f"finished_at = CASE WHEN {out_of_attempts} THEN now() END, "
        f"worker_id = CASE WHEN {out_of_attempts} THEN jobs.worker_id END, "
        "lease_until = NULL, updated_at = now(), "
        "error = 'The worker stopped responding during attempt ' || jobs.attempts "
        "|| ' (its lease ran out).' "
        "FROM expired WHERE jobs.id = expired.id RETURNING jobs.*"
    )
    params: dict[str, Any] = {"schedule": schedule, "steps": len(schedule)}
    if kinds is not None:
        params["kinds"] = list(kinds)
    with (engine or get_engine()).begin() as connection:
        rows = connection.execute(statement, params).all()
    return sorted((_job(row) for row in rows), key=lambda job: job.id)


def get_job(job_id: int, *, engine: Engine | None = None) -> Job | None:
    with (engine or get_engine()).connect() as connection:
        row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).first()
    return None if row is None else _job(row)


def active_job(
    dedupe_key: str, *, connection: Connection | None = None, engine: Engine | None = None
) -> Job | None:
    """The queued or running job that holds ``dedupe_key``, if any."""
    statement = text(
        "SELECT * FROM jobs WHERE dedupe_key = :key AND status IN ('queued', 'running')"
    )
    if connection is not None:
        row = connection.execute(statement, {"key": dedupe_key}).first()
    else:
        with (engine or get_engine()).connect() as conn:
            row = conn.execute(statement, {"key": dedupe_key}).first()
    return None if row is None else _job(row)


def latest_job(
    snapshot_id: int,
    *,
    kind: str | None = None,
    connection: Connection | None = None,
    engine: Engine | None = None,
) -> Job | None:
    """The newest job of a snapshot (of ``kind``), whatever its status."""
    kind_filter = "AND kind = :kind" if kind is not None else ""
    statement = text(
        f"SELECT * FROM jobs WHERE snapshot_id = :snapshot {kind_filter} ORDER BY id DESC LIMIT 1"
    )
    params = {"snapshot": snapshot_id, "kind": kind}
    if connection is not None:
        row = connection.execute(statement, params).first()
    else:
        with (engine or get_engine()).connect() as conn:
            row = conn.execute(statement, params).first()
    return None if row is None else _job(row)


def _finish(lease: Lease, changes: str, params: Mapping[str, Any], engine: Engine | None) -> Job:
    with (engine or get_engine()).begin() as connection:
        row = connection.execute(
            text(f"UPDATE jobs SET {changes} WHERE {_FENCE} RETURNING *"),
            {**_fence(lease), **params},
        ).first()
    if row is None:
        raise LeaseLost(f"Job {lease.job_id} attempt {lease.attempt} lost its lease.")
    return _job(row)


def _fence(lease: Lease) -> dict[str, Any]:
    return {"id": lease.job_id, "worker": lease.worker_id, "attempt": lease.attempt}


def _job(row: Row[Any]) -> Job:
    return Job.model_validate(dict(row._mapping))
