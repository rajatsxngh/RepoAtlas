"""The build job end to end: a worker runs the registered stages, reports progress, resumes
from checkpoints after a crash (in a thread, and as a real process killed mid-build), and marks
the snapshot failed only when the job fails for good."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from repoatlas.config import Settings
from repoatlas.graph import build as graph_build
from repoatlas.jobs import queue
from repoatlas.jobs import worker as worker_module
from repoatlas.jobs.build import BuildPayload, enqueue_build
from repoatlas.jobs.queue import Job
from repoatlas.jobs.worker import Worker
from repoatlas.pipeline import build as build_module
from repoatlas.pipeline import parse as parse_module
from repoatlas.pipeline.build import build_dir, resolve_target, run_build
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import Stage, register_stage, registered_stages, unregister_stage
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import begin_snapshot
from tests.conftest import TEST_EMBEDDING_MODEL
from tests.integration.origin import URL, Origin, serve_origin

pytestmark = pytest.mark.usefixtures("clean_store", "jobs_engine")

BACKEND = Path(__file__).resolve().parents[3]
WAIT_S = 60.0


class Crash(BaseException):
    """Stands in for the process dying: no worker code handles it."""


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    return serve_origin(tmp_path, monkeypatch)


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", database_url=pg_url, job_max_attempts=3
    )


def request(settings: Settings, engine: Engine) -> tuple[int, Job, BuildPayload]:
    """What a build request does: resolve, create the snapshot, enqueue its build."""
    target = resolve_target(URL, settings)
    ref = begin_snapshot(target.repo, target.commit, target.fingerprint, engine=engine)
    payload = BuildPayload.for_target(ref.id, URL, target)
    with engine.begin() as connection:
        job = enqueue_build(payload, settings, connection=connection)
    return ref.id, job, payload


def worker(settings: Settings, engine: Engine, name: str, **overrides: Any) -> Worker:
    options: dict[str, Any] = {"lease_s": 2.0, "heartbeat_s": 0.25, "poll_s": 0.05}
    options.update(overrides)
    return Worker(settings, engine=engine, worker_id=name, backoff_s=(0.0,), **options)


def snapshot(engine: Engine, snapshot_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT status, error, stats FROM snapshots WHERE id = :s"), {"s": snapshot_id}
        ).one()


def job_now(engine: Engine, job_id: int) -> Job:
    found = queue.get_job(job_id, engine=engine)
    assert found is not None
    return found


def wait_for(condition: Callable[[], object], what: str, timeout: float = WAIT_S) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out waiting for {what}.")
        time.sleep(0.05)


def test_a_build_job_builds_the_snapshot_and_reports_progress(
    origin: Origin, settings: Settings, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports: list[tuple[str | None, float]] = []
    real_report = queue.report

    def recording(lease: Any, *, stage: str | None, progress: float, engine: Engine) -> None:
        reports.append((stage, progress))
        real_report(lease, stage=stage, progress=progress, engine=engine)

    monkeypatch.setattr(worker_module, "report", recording)
    snapshot_id, job, payload = request(settings, pg_engine)
    assert job.payload == payload.model_dump(mode="json")
    assert (job.snapshot_id, job.dedupe_key, job.max_attempts) == (
        snapshot_id,
        f"build:snapshot:{snapshot_id}",
        3,
    )
    assert worker(settings, pg_engine, "w1").run_once()
    done = job_now(pg_engine, job.id)
    assert (done.status, done.progress, done.stage, done.attempts) == ("succeeded", 1, "publish", 1)
    row = snapshot(pg_engine, snapshot_id)
    assert row.status == "ready" and row.error is None
    assert row.stats["resumed"] == [] and row.stats["source"] == "github.com/pallets/flask"
    names = [stage.name for stage in registered_stages()]
    assert set(row.stats["timings"]) == set(names[:-1])  # every stage before publish
    stages = [stage for stage, _ in reports]
    assert [name for i, name in enumerate(stages) if name not in stages[:i]] == names
    values = [value for _, value in reports]
    assert values == sorted(values) and values[0] == 0.0 and 0.8 < values[-1] <= 1.0
    assert not build_dir(settings, payload.full_name, payload.commit, payload.fingerprint).exists()
    node = GraphStore(pg_engine).get_node(snapshot_id, "py:src/shop/util.py:helper:function")
    assert node is not None and node.name == "helper"


def test_a_build_resumes_from_checkpoints_after_its_worker_crashes(
    origin: Origin, settings: Settings, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_graph = graph_build.build_graph
    calls = 0

    def crash_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Crash
        return real_graph(*args, **kwargs)

    monkeypatch.setattr(build_module, "build_graph", crash_once)
    snapshot_id, job, _ = request(settings, pg_engine)
    doomed = worker(settings, pg_engine, "doomed", lease_s=1.0)
    crashed = threading.Event()

    def run() -> None:
        try:
            doomed.run_once()
        except Crash:
            crashed.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(WAIT_S)
    assert crashed.is_set()
    assert job_now(pg_engine, job.id).status == "running"
    assert snapshot(pg_engine, snapshot_id).status == "building"
    rescuer = worker(settings, pg_engine, "rescuer", lease_s=1.0)
    wait_for(rescuer.run_once, "the rescue after the lease ran out")
    done = job_now(pg_engine, job.id)
    assert (done.status, done.attempts, done.worker_id) == ("succeeded", 2, "rescuer")
    row = snapshot(pg_engine, snapshot_id)
    assert row.status == "ready"
    assert row.stats["resumed"] == ["fetch", "inventory", "parse"]
    assert (origin.fetches, origin.history_fetches) == (1, 1)  # never fetched again


def test_killing_a_worker_process_mid_build_lets_another_worker_finish_it(
    origin: Origin, settings: Settings, pg_engine: Engine, pg_url: str, tmp_path: Path
) -> None:
    snapshot_id, job, _ = request(settings, pg_engine)
    marker = tmp_path / "victim-in-graph"
    env = {key: value for key, value in os.environ.items() if not key.startswith("REPOATLAS_")}
    env.update(
        {
            "PYTHONPATH": str(BACKEND),
            "REPOATLAS_DATABASE_URL": pg_url,
            "REPOATLAS_DATA_DIR": str(settings.data_dir),
            "REPOATLAS_JOB_LEASE_S": "2",
            "REPOATLAS_JOB_HEARTBEAT_S": "0.5",
            "REPOATLAS_WORKER_POLL_S": "0.1",
            "REPOATLAS_LOG_LEVEL": "WARNING",
            "REPOATLAS_EMBEDDING_MODEL": TEST_EMBEDDING_MODEL,
        }
    )
    victim = subprocess.Popen(
        [sys.executable, "-m", "tests.integration.jobs.worker_process", origin.repo.url, marker],
        cwd=tmp_path,  # no .env above it
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for(lambda: marker.exists() or victim.poll() is not None, "the graph stage")
        assert victim.poll() is None, victim.stdout.read() if victim.stdout else ""
        running = job_now(pg_engine, job.id)
        assert (running.status, running.worker_id, running.stage) == ("running", "victim", "graph")
        victim.kill()  # TerminateProcess on Windows, SIGKILL elsewhere: no cleanup at all
        victim.wait(WAIT_S)
    finally:
        if victim.poll() is None:
            victim.kill()
        if victim.stdout is not None:
            victim.stdout.close()
    assert job_now(pg_engine, job.id).status == "running"  # nobody has noticed yet
    rescuer = worker(settings, pg_engine, "rescuer")
    wait_for(rescuer.run_once, "the rescue after the lease ran out")
    done = job_now(pg_engine, job.id)
    assert (done.status, done.attempts, done.worker_id) == ("succeeded", 2, "rescuer")
    row = snapshot(pg_engine, snapshot_id)
    assert row.status == "ready"
    assert row.stats["resumed"] == ["fetch", "inventory", "parse"]
    assert origin.fetches == 0  # the rescuer reused what the killed worker fetched


def test_retries_keep_the_snapshot_building_and_a_final_failure_marks_it_failed(
    origin: Origin,
    settings: Settings,
    pg_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("graph exploded")

    monkeypatch.setattr(build_module, "build_graph", broken)
    snapshot_id, job, payload = request(
        settings.model_copy(update={"job_max_attempts": 2}), pg_engine
    )
    runner = worker(settings, pg_engine, "w")
    runner.run_once()
    retrying = job_now(pg_engine, job.id)
    assert (retrying.status, retrying.attempts) == ("queued", 1)
    assert retrying.error == "RuntimeError: graph exploded"
    assert snapshot(pg_engine, snapshot_id).status == "building"
    workdir = build_dir(settings, payload.full_name, payload.commit, payload.fingerprint)
    assert (workdir / "parse.json").exists()  # kept for the retry
    runner.run_once()
    final = job_now(pg_engine, job.id)
    assert (final.status, final.attempts) == ("failed", 2)
    row = snapshot(pg_engine, snapshot_id)
    assert (row.status, row.error) == ("failed", "RuntimeError: graph exploded")
    assert not workdir.exists()
    assert origin.fetches == 1  # the retry resumed from the checkpoints


def test_a_worker_with_another_pipeline_refuses_the_build(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    snapshot_id, job, _ = request(settings, pg_engine)
    other = settings.model_copy(update={"max_file_bytes": 10})  # changes the fingerprint
    worker(other, pg_engine, "w").run_once()
    final = job_now(pg_engine, job.id)
    assert (final.status, final.attempts) == ("failed", 1)  # never retried
    assert final.error is not None and final.error.startswith("PipelineMismatch: ")
    row = snapshot(pg_engine, snapshot_id)
    assert row.status == "failed" and "same code and settings" in row.error
    assert origin.fetches == 0


def test_a_job_for_a_snapshot_that_is_already_ready_does_nothing(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    built = run_build(URL, settings, engine=pg_engine)
    snapshot_id, job, _ = request(settings, pg_engine)
    assert snapshot_id == built.snapshot_id
    worker(settings, pg_engine, "w").run_once()
    assert job_now(pg_engine, job.id).status == "succeeded"
    assert origin.fetches == 1  # only the first build fetched


def test_a_graceful_stop_releases_the_build_between_stages_and_it_resumes(
    origin: Origin, settings: Settings, pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = worker(settings, pg_engine, "first")
    real_parse = parse_module.parse_files

    def parse_then_stop(*args: Any, **kwargs: Any) -> Any:
        first.request_stop()  # Ctrl+C arrives while parsing
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(build_module, "parse_files", parse_then_stop)
    snapshot_id, job, _ = request(settings, pg_engine)
    first.run_once()
    released = job_now(pg_engine, job.id)
    assert (released.status, released.attempts, released.stage) == ("queued", 0, "parse")
    monkeypatch.setattr(build_module, "parse_files", real_parse)
    worker(settings, pg_engine, "second").run_once()
    done = job_now(pg_engine, job.id)
    assert (done.status, done.attempts) == ("succeeded", 1)
    assert snapshot(pg_engine, snapshot_id).stats["resumed"] == ["fetch", "inventory", "parse"]


def test_registering_a_stage_gives_new_builds_a_new_snapshot(
    origin: Origin, settings: Settings, pg_engine: Engine
) -> None:
    before = run_build(URL, settings, engine=pg_engine)
    ran: list[int | None] = []

    def extra(ctx: BuildContext) -> None:
        ran.append(ctx.snapshot_id)

    register_stage(Stage("extra", 1, (), extra))
    try:
        after = run_build(URL, settings, engine=pg_engine)
        assert after.snapshot_id != before.snapshot_id and not after.reused
        assert after.fingerprint != before.fingerprint
        assert ran == [after.snapshot_id]
        assert [stage.name for stage in registered_stages()][-1] == "extra"
        again = run_build(URL, settings, engine=pg_engine)
        assert (again.snapshot_id, again.reused) == (after.snapshot_id, True)
    finally:
        unregister_stage("extra")
    assert run_build(URL, settings, engine=pg_engine).snapshot_id == before.snapshot_id
