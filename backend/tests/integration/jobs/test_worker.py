"""The worker loop, run in threads with short leases: success, retries, crashes and recovery,
heartbeats, graceful stops, timeouts, and lost leases."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.config import Settings, get_settings
from repoatlas.jobs import queue
from repoatlas.jobs.queue import Job
from repoatlas.jobs.worker import JobContext, JobHandler, Worker
from tests.integration.jobs.conftest import row

WAIT_S = 20.0  # generous: CI machines are slow


class Crash(BaseException):
    """Stands in for the process dying: no worker code handles it."""


def settings_for(pg_url: str, tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_url=pg_url, data_dir=tmp_path / "data")


def make_worker(
    engine: Engine,
    settings: Settings,
    handlers: dict[str, JobHandler],
    **overrides: Any,
) -> Worker:
    options: dict[str, Any] = {
        "lease_s": 2.0,
        "heartbeat_s": 0.2,
        "poll_s": 0.05,
        "backoff_s": (0.0,),
    }
    options.update(overrides)
    return Worker(settings, engine=engine, handlers=handlers, **options)


def wait_for(condition: Callable[[], bool], what: str, timeout: float = WAIT_S) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out waiting for {what}.")
        time.sleep(0.02)


def status(engine: Engine, job_id: int) -> str:
    return str(row(engine, job_id).status)


@pytest.fixture
def settings(pg_url: str, tmp_path: Path) -> Settings:
    return settings_for(pg_url, tmp_path)


class Background:
    """Runs a worker in a thread; a ``Crash`` ends the thread the way a killed process ends."""

    def __init__(self, worker: Worker, **run: Any) -> None:
        self.worker = worker
        self.crashed = False
        self.thread = threading.Thread(target=self._run, kwargs=run, daemon=True)
        self.thread.start()

    def _run(self, **run: Any) -> None:
        try:
            self.worker.run(**run)
        except Crash:
            self.crashed = True

    def join(self) -> None:
        self.thread.join(WAIT_S)
        assert not self.thread.is_alive(), "the worker did not stop"


@pytest.fixture
def background() -> Iterator[Callable[..., Background]]:
    started: list[Background] = []

    def start(worker: Worker, **run: Any) -> Background:
        started.append(Background(worker, **run))
        return started[-1]

    yield start
    for runner in started:
        runner.worker.request_stop()
        runner.thread.join(WAIT_S)


def test_a_worker_runs_a_job_and_reports_progress(jobs_engine: Engine, settings: Settings) -> None:
    seen: list[tuple[str, Any]] = []

    def build(ctx: JobContext) -> None:
        seen.append(("payload", ctx.job.payload))
        ctx.report("fetch", 0.25)
        ctx.report("fetch", 0.3)  # throttled: same stage within a second
        assert row(jobs_engine, ctx.job.id).progress == 0.25
        ctx.report("parse", 0.5)  # a new stage is always written
        assert (row(jobs_engine, ctx.job.id).stage, row(jobs_engine, ctx.job.id).progress) == (
            "parse",
            0.5,
        )
        ctx.check()

    job = queue.enqueue("build", {"url": "https://github.com/a/b"}, engine=jobs_engine)
    worker = make_worker(jobs_engine, settings, {"build": JobHandler("build", build)})
    assert worker.run_once() is True
    assert seen == [("payload", {"url": "https://github.com/a/b"})]
    done = queue.get_job(job.id, engine=jobs_engine)
    assert done is not None and (done.status, done.progress, done.attempts) == ("succeeded", 1, 1)
    assert worker.run_once() is False  # nothing left
    assert worker.processed == 1


def test_failures_are_retried_then_fail_for_good_once(
    jobs_engine: Engine, settings: Settings
) -> None:
    failed_for_good: list[Job] = []

    def broken(ctx: JobContext) -> None:
        raise RuntimeError(f"attempt {ctx.job.attempts} broke")

    handler = JobHandler(
        "build", broken, on_failed=lambda job, _engine: failed_for_good.append(job)
    )
    job = queue.enqueue("build", engine=jobs_engine)
    worker = make_worker(jobs_engine, settings, {"build": handler})
    for _ in range(3):
        assert worker.run_once() is True
    assert worker.run_once() is False
    final = queue.get_job(job.id, engine=jobs_engine)
    assert final is not None and (final.status, final.attempts) == ("failed", 3)
    assert final.error == "RuntimeError: attempt 3 broke"
    assert [job.id for job in failed_for_good] == [job.id]


def test_permanent_errors_fail_at_once(jobs_engine: Engine, settings: Settings) -> None:
    def invalid(_ctx: JobContext) -> None:
        raise ValueError("this repository can never build")

    handler = JobHandler("build", invalid, permanent_errors=(ValueError,))
    job = queue.enqueue("build", engine=jobs_engine)
    make_worker(jobs_engine, settings, {"build": handler}).run_once()
    final = queue.get_job(job.id, engine=jobs_engine)
    assert final is not None and (final.status, final.attempts) == ("failed", 1)


def test_heartbeats_keep_a_long_job_from_being_taken(
    jobs_engine: Engine, settings: Settings, background: Callable[..., Background]
) -> None:
    def slow(ctx: JobContext) -> None:
        deadline = time.monotonic() + 2.5  # longer than the 1 s lease
        while time.monotonic() < deadline:
            ctx.check()
            time.sleep(0.05)

    handlers = {"build": JobHandler("build", slow)}
    job = queue.enqueue("build", engine=jobs_engine)
    first = background(
        make_worker(jobs_engine, settings, handlers, lease_s=1.0, worker_id="w1"), max_jobs=1
    )
    wait_for(lambda: status(jobs_engine, job.id) == "running", "the first claim")
    rival = make_worker(jobs_engine, settings, handlers, lease_s=1.0, worker_id="w2")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:  # the rival keeps trying while the lease is renewed
        assert rival.run_once() is False
        time.sleep(0.1)
    first.join()
    done = queue.get_job(job.id, engine=jobs_engine)
    assert done is not None and (done.status, done.attempts, done.worker_id) == (
        "succeeded",
        1,
        "w1",
    )


def test_a_crashed_workers_job_is_resumed_by_another_after_its_lease_expires(
    jobs_engine: Engine, settings: Settings, background: Callable[..., Background]
) -> None:
    calls: list[str] = []
    started = threading.Event()

    def build(ctx: JobContext) -> None:
        calls.append(ctx.lease.worker_id)
        if ctx.lease.worker_id == "doomed":
            started.set()
            raise Crash  # the process dies mid-build: no heartbeat, no completion

    handlers = {"build": JobHandler("build", build)}
    job = queue.enqueue("build", engine=jobs_engine)
    doomed = background(
        make_worker(jobs_engine, settings, handlers, lease_s=1.0, worker_id="doomed")
    )
    assert started.wait(WAIT_S)
    doomed.join()
    assert doomed.crashed
    assert status(jobs_engine, job.id) == "running"  # nobody knows yet
    rescuer = make_worker(jobs_engine, settings, handlers, lease_s=1.0, worker_id="rescuer")
    assert rescuer.run_once() is False  # the lease has not run out yet
    wait_for(lambda: rescuer.run_once(), "the rescue")
    done = queue.get_job(job.id, engine=jobs_engine)
    assert done is not None and (done.status, done.attempts, done.worker_id) == (
        "succeeded",
        2,
        "rescuer",
    )
    assert calls == ["doomed", "rescuer"]


def test_running_out_of_attempts_through_crashes_calls_on_failed(
    jobs_engine: Engine, settings: Settings
) -> None:
    failed: list[int] = []
    handler = JobHandler(
        "build", lambda _ctx: None, on_failed=lambda job, _engine: failed.append(job.id)
    )
    job = queue.enqueue("build", max_attempts=1, engine=jobs_engine)
    claimed = queue.claim("gone", engine=jobs_engine)
    assert claimed is not None
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET lease_until = now() - interval '1 second'"))
    worker = make_worker(jobs_engine, settings, {"build": handler})
    assert worker.run_once() is False
    assert status(jobs_engine, job.id) == "failed" and failed == [job.id]


def test_a_graceful_stop_releases_the_job_at_the_next_check(
    jobs_engine: Engine, settings: Settings, background: Callable[..., Background]
) -> None:
    inside = threading.Event()
    stages: list[str] = []

    def build(ctx: JobContext) -> None:
        for stage in ("fetch", "parse", "graph"):
            ctx.check()
            stages.append(stage)
            ctx.report(stage, len(stages) / 3, force=True)
            if stage == "fetch":
                inside.set()
                time.sleep(0.5)  # the stop request arrives during this stage

    job = queue.enqueue("build", engine=jobs_engine)
    runner = background(make_worker(jobs_engine, settings, {"build": JobHandler("build", build)}))
    assert inside.wait(WAIT_S)
    runner.worker.request_stop()
    runner.join()
    assert stages == ["fetch"]
    released = queue.get_job(job.id, engine=jobs_engine)
    assert released is not None
    assert (released.status, released.attempts, released.stage) == ("queued", 0, "fetch")
    assert runner.worker.processed == 1


def test_errors_while_stopping_release_the_job_instead_of_failing_it(
    jobs_engine: Engine, settings: Settings
) -> None:
    worker_box: list[Worker] = []

    def build(_ctx: JobContext) -> None:
        worker_box[0].request_stop()  # Ctrl+C arrived, and git died of it too
        raise OSError("git was interrupted")

    job = queue.enqueue("build", engine=jobs_engine)
    worker = make_worker(jobs_engine, settings, {"build": JobHandler("build", build)})
    worker_box.append(worker)
    worker.run_once()
    released = queue.get_job(job.id, engine=jobs_engine)
    assert released is not None and (released.status, released.attempts) == ("queued", 0)


def test_a_stop_file_stops_an_idle_worker(
    jobs_engine: Engine, settings: Settings, tmp_path: Path, background: Callable[..., Background]
) -> None:
    stop = tmp_path / "worker.stop"
    stop.write_text("left over from last time\n")
    runner = background(make_worker(jobs_engine, settings, {}, stop_file=stop))
    time.sleep(0.3)
    assert runner.thread.is_alive()  # an old stop file is not a stop request
    stop.write_text("stop now, please\n")
    runner.join()


def test_an_attempt_that_runs_too_long_fails_and_is_retried(
    jobs_engine: Engine, settings: Settings
) -> None:
    def endless(ctx: JobContext) -> None:
        while True:
            ctx.check()
            time.sleep(0.02)

    job = queue.enqueue("build", engine=jobs_engine)
    worker = make_worker(
        jobs_engine, settings, {"build": JobHandler("build", endless)}, timeout_s=0.3
    )
    worker.run_once()
    after = queue.get_job(job.id, engine=jobs_engine)
    assert after is not None and (after.status, after.attempts) == ("queued", 1)
    assert after.error == "The attempt ran longer than the 0.3 s limit."


def test_a_worker_that_lost_its_lease_walks_away(jobs_engine: Engine, settings: Settings) -> None:
    def build(ctx: JobContext) -> None:
        with jobs_engine.begin() as connection:  # another worker took the job meanwhile
            connection.execute(text("UPDATE jobs SET worker_id = 'thief', attempts = attempts + 1"))
        ctx.report("parse", 0.5)

    job = queue.enqueue("build", engine=jobs_engine)
    result = make_worker(jobs_engine, settings, {"build": JobHandler("build", build)}).process(
        queue.claim("w", engine=jobs_engine)  # type: ignore[arg-type]
    )
    assert result is None
    after = row(jobs_engine, job.id)
    assert (after.status, after.worker_id, after.attempts) == ("running", "thief", 2)


def test_heartbeats_must_come_more_often_than_the_lease_lasts(
    jobs_engine: Engine, settings: Settings
) -> None:
    with pytest.raises(ValueError, match="Heartbeats"):
        Worker(settings, engine=jobs_engine, handlers={}, lease_s=1, heartbeat_s=1)


@pytest.fixture
def cli_env(pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", pg_url)
    monkeypatch.setenv("REPOATLAS_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield tmp_path / "data"
    get_settings.cache_clear()


def test_the_worker_command_runs_due_jobs_once(jobs_engine: Engine, cli_env: Path) -> None:
    result = CliRunner().invoke(app, ["worker", "--once", "--id", "cli-worker"])
    assert result.exit_code == 0, result.output
    assert "Worker cli-worker" in result.output
    assert str(cli_env / "worker.stop") in result.output.replace("\n", "")
    assert "stopped after 0 job(s)" in result.output
