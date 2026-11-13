"""The worker (``repoatlas worker``): claims jobs and runs their handlers (API-3).

A worker runs one job at a time in the calling thread, while a heartbeat thread extends the
job's lease every ``heartbeat_s``. Before each claim it requeues jobs whose lease ran out, so any
worker recovers the jobs of one that crashed or was killed.

A handler runs one attempt. At safe points (between stages) it calls ``ctx.check()``, which
raises ``JobInterrupted`` when the worker is stopping, the attempt has run out of time, or the
lease was lost. A stopping worker releases its job, so the next worker starts it again without
losing an attempt; a timed-out attempt fails like any error; a lost lease means another worker
owns the job, so this one walks away without writing anything. An exception fails the attempt,
which is retried with backoff unless the handler lists the error as permanent; when a job fails
for good, its handler's ``on_failed`` runs (a build marks its snapshot failed).

Stopping: ``request_stop()`` (the CLI calls it on Ctrl+C, Ctrl+Break, or SIGTERM), or a stop
file that appears or changes after the worker started; Windows consoles send no SIGTERM, so the
file is the portable way. Either way the current attempt stops at its next check and is
released. An error raised while the worker is stopping is treated the same way, because console
signals also reach child processes such as ``git``.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import secrets
import socket
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.db.engine import create_db_engine
from repoatlas.jobs.queue import (
    Job,
    LeaseLost,
    claim,
    complete,
    fail,
    heartbeat,
    recover_expired,
    release,
    report,
)
from repoatlas.logging import get_logger

InterruptReason = Literal["stop", "timeout", "lease_lost"]
REPORT_INTERVAL_S = 1.0  # progress writes per attempt, at most one per interval and stage
IDLE_SLICE_S = 0.2  # an idle worker notices a stop request within this long
# Modules whose import registers the built-in job handlers.
BUILTIN_HANDLER_MODULES: tuple[str, ...] = ("repoatlas.jobs.build",)

log = get_logger(__name__)

_MESSAGES: dict[InterruptReason, str] = {
    "stop": "The worker is stopping.",
    "timeout": "The attempt ran out of time.",
    "lease_lost": "The worker lost the job's lease.",
}


class JobInterrupted(Exception):
    """Raised by ``JobContext.check`` (and ``report``) when the attempt must stop."""

    def __init__(self, reason: InterruptReason) -> None:
        super().__init__(_MESSAGES[reason])
        self.reason: InterruptReason = reason


@dataclass(frozen=True)
class JobHandler:
    """What a worker does with jobs of ``kind``. ``on_failed(job, settings, engine)`` runs once
    when a job fails for good; errors in ``permanent_errors`` are never retried."""

    kind: str
    run: Callable[[JobContext], None]
    on_failed: Callable[[Job, Settings, Engine], None] | None = None
    permanent_errors: tuple[type[BaseException], ...] = ()


_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(handler: JobHandler) -> None:
    if handler.kind in _HANDLERS:
        raise ValueError(f"A handler for {handler.kind!r} jobs is already registered.")
    _HANDLERS[handler.kind] = handler


def unregister_job_handler(kind: str) -> None:
    _HANDLERS.pop(kind, None)


def job_handlers() -> dict[str, JobHandler]:
    """Every registered handler by kind (the built-in ones included)."""
    for module in BUILTIN_HANDLER_MODULES:
        importlib.import_module(module)
    return dict(_HANDLERS)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


class _Heartbeat:
    """Extends a lease every ``interval_s`` until stopped, the lease is lost, or the attempt's
    time is up (then the lease is left to run out)."""

    def __init__(self, job: Job, worker: Worker) -> None:
        self._lease = job.lease
        self._worker = worker
        self._done = threading.Event()
        self.lost = False
        self._thread = threading.Thread(
            target=self._beat, name=f"heartbeat-job-{job.id}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._done.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=self._worker.heartbeat_s + 5)

    def _beat(self) -> None:
        started = time.monotonic()
        worker = self._worker
        while not self._done.wait(worker.heartbeat_s):
            if time.monotonic() - started >= worker.timeout_s:
                return  # out of time: stop renewing, so a hung attempt loses its lease
            try:
                heartbeat(self._lease, lease_s=worker.lease_s, engine=worker.engine)
            except LeaseLost:
                self.lost = True
                log.warning("lease lost", job_id=self._lease.job_id)
                return
            except Exception:  # a database blip: the next beat tries again
                log.warning("heartbeat failed", job_id=self._lease.job_id, exc_info=True)


class JobContext:
    """What a handler gets: the job, the worker's settings and engine, progress reporting, and
    the interruption check."""

    def __init__(self, job: Job, worker: Worker, beat: _Heartbeat) -> None:
        self.job = job
        self.lease = job.lease
        self.settings = worker.settings
        self.engine = worker.engine
        self._worker = worker
        self._beat = beat
        self._started = time.monotonic()
        self._lost = False
        self._last_stage: str | None = None
        self._last_report = float("-inf")

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._started

    def stop_reason(self) -> InterruptReason | None:
        if self._lost or self._beat.lost:
            return "lease_lost"
        if self._worker.stopping:
            return "stop"
        if self.elapsed_s >= self._worker.timeout_s:
            return "timeout"
        return None

    def check(self) -> None:
        """Raise ``JobInterrupted`` if the attempt must stop now."""
        reason = self.stop_reason()
        if reason is not None:
            raise JobInterrupted(reason)

    def report(self, stage: str | None, progress: float, *, force: bool = False) -> None:
        """Record the stage and overall progress (0 to 1). Writes happen when the stage
        changes, when ``force`` is set, or at most once per ``REPORT_INTERVAL_S``."""
        now = time.monotonic()
        if not force and stage == self._last_stage and now - self._last_report < REPORT_INTERVAL_S:
            return
        try:
            report(self.lease, stage=stage, progress=progress, engine=self.engine)
        except LeaseLost:
            self._lost = True
            raise JobInterrupted("lease_lost") from None
        self._last_stage, self._last_report = stage, now


def _stamp(path: Path | None) -> tuple[int, int] | None:
    """The stop file's modification time and size, or ``None`` when it doesn't exist."""
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


class Worker:
    """Claims due jobs of the kinds it has handlers for and runs them one at a time."""

    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        handlers: Mapping[str, JobHandler] | None = None,
        worker_id: str | None = None,
        stop_file: Path | None = None,
        lease_s: float | None = None,
        heartbeat_s: float | None = None,
        poll_s: float | None = None,
        timeout_s: float | None = None,
        backoff_s: Sequence[float] | None = None,
    ) -> None:
        self.settings = settings
        self.lease_s = settings.job_lease_s if lease_s is None else lease_s
        self.heartbeat_s = settings.job_heartbeat_s if heartbeat_s is None else heartbeat_s
        self.poll_s = settings.worker_poll_s if poll_s is None else poll_s
        self.timeout_s = settings.job_timeout_s if timeout_s is None else timeout_s
        self.backoff_s = tuple(settings.job_retry_backoff_s if backoff_s is None else backoff_s)
        self.max_running = settings.job_max_running or None  # 0: no cap
        if not 0 < self.heartbeat_s < self.lease_s:
            raise ValueError("Heartbeats must be more frequent than the lease is long.")
        if self.timeout_s <= 0 or self.poll_s < 0:
            raise ValueError(
                "The attempt timeout must be positive and the poll interval not negative."
            )
        self.handlers = dict(job_handlers() if handlers is None else handlers)
        self.id = worker_id or default_worker_id()
        self._owns_engine = engine is None
        self.engine = engine or create_db_engine(
            settings.database_url, connect_timeout_s=settings.db_connect_timeout_s
        )
        self._stop = threading.Event()
        self.stop_file = stop_file
        self._stop_stamp = _stamp(stop_file)
        self.processed = 0

    @property
    def kinds(self) -> list[str]:
        return sorted(self.handlers)

    # --- stopping ---------------------------------------------------------------------------

    def request_stop(self) -> None:
        """Stop after the current attempt reaches its next check (thread- and signal-safe)."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        if self._stop.is_set():
            return True
        if self.stop_file is not None and _stamp(self.stop_file) != self._stop_stamp:
            log.info("stop file changed", path=str(self.stop_file))
            self._stop.set()
            return True
        return False

    # --- the loop ---------------------------------------------------------------------------

    def run(self, *, max_jobs: int | None = None) -> int:
        """Process jobs until stopped (or after ``max_jobs``); return how many were processed."""
        log.info("worker started", worker_id=self.id, kinds=self.kinds)
        try:
            while not self.stopping and (max_jobs is None or self.processed < max_jobs):
                if not self.run_once():
                    self._idle()
        finally:
            log.info("worker stopped", worker_id=self.id, processed=self.processed)
            self.close()
        return self.processed

    def run_once(self) -> bool:
        """Recover expired leases, then claim and process one due job (``False``: none)."""
        self.recover()
        if self.stopping:
            return False
        job = claim(
            self.id,
            kinds=self.kinds,
            lease_s=self.lease_s,
            max_running=self.max_running,
            engine=self.engine,
        )
        if job is None:
            return False
        self.process(job)
        self.processed += 1
        return True

    def recover(self) -> list[Job]:
        """Requeue (or fail) jobs of this worker's kinds whose lease ran out."""
        recovered = recover_expired(kinds=self.kinds, backoff_s=self.backoff_s, engine=self.engine)
        for job in recovered:
            log.warning(
                "lease expired",
                job_id=job.id,
                kind=job.kind,
                attempts=job.attempts,
                status=job.status,
            )
            if job.status == "failed":
                self._failed_for_good(job)
        return recovered

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def _idle(self) -> None:
        deadline = time.monotonic() + self.poll_s
        while not self.stopping:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, IDLE_SLICE_S))

    # --- one job ----------------------------------------------------------------------------

    def process(self, job: Job) -> Job | None:
        """Run one attempt of a job this worker claimed; return the job as it was left (``None``
        when the lease was lost and another worker owns it now)."""
        handler = self.handlers[job.kind]
        beat = _Heartbeat(job, self)
        ctx = JobContext(job, self, beat)
        with structlog.contextvars.bound_contextvars(
            job_id=job.id, kind=job.kind, attempt=job.attempts, worker_id=self.id
        ):
            log.info("job started", snapshot_id=job.snapshot_id)
            beat.start()
            try:
                handler.run(ctx)
            except JobInterrupted as interrupted:
                beat.stop()
                return self._interrupted(ctx, interrupted.reason)
            except Exception as error:
                beat.stop()
                return self._failed(ctx, handler, error)
            except BaseException as error:  # KeyboardInterrupt (a second Ctrl+C), SystemExit
                beat.stop()
                if isinstance(error, KeyboardInterrupt):
                    with contextlib.suppress(Exception):
                        release(ctx.lease, engine=self.engine)
                raise
            beat.stop()
            if ctx.stop_reason() == "lease_lost":
                log.warning("job finished after its lease was lost")
                return None
            try:
                done = complete(ctx.lease, engine=self.engine)
            except LeaseLost:
                log.warning("job finished after its lease was lost")
                return None
            log.info("job succeeded", seconds=round(ctx.elapsed_s, 3))
            return done

    def _interrupted(self, ctx: JobContext, reason: InterruptReason) -> Job | None:
        try:
            if reason == "stop":
                released = release(ctx.lease, engine=self.engine)
                log.info("job released", stage=released.stage)
                return released
            if reason == "timeout":
                failed = fail(
                    ctx.lease,
                    f"The attempt ran longer than the {self.timeout_s:g} s limit.",
                    backoff_s=self.backoff_s,
                    engine=self.engine,
                )
                log.warning("job attempt timed out", status=failed.status)
                if failed.status == "failed":
                    self._failed_for_good(failed)
                return failed
        except LeaseLost:
            pass
        log.warning("job abandoned: another worker owns it now")
        return None

    def _failed(self, ctx: JobContext, handler: JobHandler, error: Exception) -> Job | None:
        if ctx.stop_reason() == "lease_lost":
            log.warning("job failed after its lease was lost", error=str(error))
            return None
        if self.stopping:  # console signals reach child processes too; don't blame the job
            log.warning("job attempt failed while stopping; released", error=str(error))
            return self._interrupted(ctx, "stop")
        permanent = isinstance(error, handler.permanent_errors)
        try:
            failed = fail(
                ctx.lease, error, retry=not permanent, backoff_s=self.backoff_s, engine=self.engine
            )
        except LeaseLost:
            log.warning("job failed after its lease was lost", error=str(error))
            return None
        log.warning(
            "job attempt failed",
            error=failed.error,
            status=failed.status,
            permanent=permanent,
            exc_info=not permanent,
        )
        if failed.status == "failed":
            self._failed_for_good(failed)
        return failed

    def _failed_for_good(self, job: Job) -> None:
        handler = self.handlers.get(job.kind)
        if handler is None or handler.on_failed is None:
            return
        try:
            handler.on_failed(job, self.settings, self.engine)
        except Exception:
            log.exception("on_failed hook failed", job_id=job.id, kind=job.kind)
