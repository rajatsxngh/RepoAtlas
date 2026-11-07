"""The job queue against Postgres: enqueue, exclusive claims, fencing, retries, lease recovery."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from repoatlas.jobs import queue
from repoatlas.jobs.queue import Lease, LeaseLost, backoff_delay, error_text
from tests.integration.jobs.conftest import age, row

SHA = "a" * 40


def test_enqueue_creates_a_due_queued_job(jobs_engine: Engine) -> None:
    job = queue.enqueue(
        "build", {"snapshot_id": 7, "url": "https://github.com/a/b"}, engine=jobs_engine
    )
    assert (job.kind, job.status, job.attempts, job.max_attempts) == ("build", "queued", 0, 3)
    assert job.payload == {"snapshot_id": 7, "url": "https://github.com/a/b"}
    assert (job.stage, job.progress, job.worker_id, job.lease_until, job.error) == (
        None,
        0.0,
        None,
        None,
        None,
    )
    assert abs(row(jobs_engine, job.id).due_in) < 5
    later = queue.enqueue("build", delay_s=30, max_attempts=5, engine=jobs_engine)
    assert later.max_attempts == 5 and 25 < row(jobs_engine, later.id).due_in <= 30
    with pytest.raises(ValueError):
        queue.enqueue("build", max_attempts=0, engine=jobs_engine)
    with pytest.raises(ValueError):
        queue.enqueue("", engine=jobs_engine)


def test_a_dedupe_key_allows_one_active_job(jobs_engine: Engine) -> None:
    first = queue.enqueue("build", {"n": 1}, dedupe_key="build:1", engine=jobs_engine)
    again = queue.enqueue("build", {"n": 2}, dedupe_key="build:1", engine=jobs_engine)
    assert again.id == first.id and again.payload == {"n": 1}
    other = queue.enqueue("build", dedupe_key="build:2", engine=jobs_engine)
    assert other.id != first.id
    claimed = queue.claim("w1", engine=jobs_engine)
    assert claimed is not None and claimed.id == first.id
    while_running = queue.enqueue("build", dedupe_key="build:1", engine=jobs_engine)
    assert while_running.id == first.id
    queue.complete(claimed.lease, engine=jobs_engine)
    after = queue.enqueue("build", dedupe_key="build:1", engine=jobs_engine)
    assert after.id not in (first.id, other.id)
    unkeyed = [queue.enqueue("build", engine=jobs_engine).id for _ in range(2)]
    assert len(set(unkeyed)) == 2
    with jobs_engine.begin() as connection, pytest.raises(IntegrityError):  # the index holds
        connection.execute(text("INSERT INTO jobs (kind, dedupe_key) VALUES ('build', 'build:2')"))


def test_enqueue_joins_the_callers_transaction(jobs_engine: Engine) -> None:
    with jobs_engine.connect() as connection:
        transaction = connection.begin()
        job = queue.enqueue("build", connection=connection)
        transaction.rollback()
    assert queue.get_job(job.id, engine=jobs_engine) is None


def test_claim_takes_the_oldest_due_job_of_the_wanted_kinds(jobs_engine: Engine) -> None:
    first = queue.enqueue("build", engine=jobs_engine)
    queue.enqueue("build", delay_s=60, engine=jobs_engine)  # not due yet
    other_kind = queue.enqueue("eval", engine=jobs_engine)
    second = queue.enqueue("build", engine=jobs_engine)
    assert queue.claim("w", kinds=[], engine=jobs_engine) is None
    claimed = queue.claim("w", kinds=["build"], lease_s=30, engine=jobs_engine)
    assert claimed is not None and claimed.id == first.id
    assert (claimed.status, claimed.worker_id, claimed.attempts) == ("running", "w", 1)
    assert claimed.started_at is not None and claimed.heartbeat_at is not None
    assert 25 < row(jobs_engine, first.id).lease_left <= 30
    assert claimed.lease == Lease(job_id=first.id, worker_id="w", attempt=1)
    nxt = queue.claim("w", kinds=["build"], engine=jobs_engine)
    assert nxt is not None and nxt.id == second.id
    assert queue.claim("w", kinds=["build"], engine=jobs_engine) is None
    anything = queue.claim("w", engine=jobs_engine)
    assert anything is not None and anything.id == other_kind.id


def test_each_job_is_claimed_by_exactly_one_of_many_workers(jobs_engine: Engine) -> None:
    ids = {queue.enqueue("build", engine=jobs_engine).id for _ in range(40)}
    start = threading.Barrier(4)

    def drain(worker: str) -> list[int]:
        start.wait()
        claimed: list[int] = []
        while (job := queue.claim(worker, engine=jobs_engine)) is not None:
            claimed.append(job.id)
        return claimed

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(drain, ["w1", "w2", "w3", "w4"]))
    every = [job_id for claimed in results for job_id in claimed]
    assert sorted(every) == sorted(ids)  # every job once, none twice
    assert sum(1 for claimed in results if claimed) >= 2  # the work was shared
    with jobs_engine.connect() as connection:
        owners = connection.execute(
            text("SELECT worker_id, count(*) FROM jobs GROUP BY worker_id")
        ).all()
    assert sum(count for _, count in owners) == 40


def test_a_running_job_cannot_be_claimed_again(jobs_engine: Engine) -> None:
    queue.enqueue("build", engine=jobs_engine)
    assert queue.claim("w1", engine=jobs_engine) is not None
    assert queue.claim("w2", engine=jobs_engine) is None


def test_heartbeats_extend_the_lease_and_report_records_progress(jobs_engine: Engine) -> None:
    queue.enqueue("build", engine=jobs_engine)
    job = queue.claim("w", lease_s=5, engine=jobs_engine)
    assert job is not None
    until = queue.heartbeat(job.lease, lease_s=120, engine=jobs_engine)
    assert until > job.lease_until  # type: ignore[operator]
    assert 115 < row(jobs_engine, job.id).lease_left <= 120
    queue.report(job.lease, stage="parse", progress=0.25, engine=jobs_engine)
    assert (row(jobs_engine, job.id).stage, row(jobs_engine, job.id).progress) == ("parse", 0.25)
    queue.report(job.lease, stage="graph", progress=1.7, engine=jobs_engine)
    assert row(jobs_engine, job.id).progress == 1.0
    done = queue.complete(job.lease, engine=jobs_engine)
    assert (done.status, done.progress, done.lease_until, done.error) == (
        "succeeded",
        1.0,
        None,
        None,
    )
    assert done.finished_at is not None


def test_a_failed_attempt_is_retried_with_backoff_until_attempts_run_out(
    jobs_engine: Engine,
) -> None:
    job = queue.enqueue("build", engine=jobs_engine)
    for attempt, delay in ((1, 10), (2, 60)):
        claimed = queue.claim("w", engine=jobs_engine)
        assert claimed is not None and claimed.attempts == attempt
        failed = queue.fail(claimed.lease, RuntimeError(f"boom {attempt}"), engine=jobs_engine)
        assert (failed.status, failed.worker_id, failed.lease_until) == ("queued", None, None)
        assert failed.error == f"RuntimeError: boom {attempt}"
        assert delay - 5 < row(jobs_engine, job.id).due_in <= delay
        assert queue.claim("w", engine=jobs_engine) is None  # not due yet
        age(jobs_engine, job.id, due=True)
    last = queue.claim("w", engine=jobs_engine)
    assert last is not None and last.attempts == 3
    assert last.error is None  # a new attempt starts clean
    final = queue.fail(last.lease, "out of luck", engine=jobs_engine)
    assert (final.status, final.attempts, final.error) == ("failed", 3, "out of luck")
    assert final.finished_at is not None and final.worker_id == "w"
    assert queue.claim("w", engine=jobs_engine) is None


def test_permanent_failures_are_not_retried(jobs_engine: Engine) -> None:
    queue.enqueue("build", engine=jobs_engine)
    claimed = queue.claim("w", engine=jobs_engine)
    assert claimed is not None
    failed = queue.fail(claimed.lease, ValueError("bad input"), retry=False, engine=jobs_engine)
    assert (failed.status, failed.attempts) == ("failed", 1)


def test_expired_leases_count_as_failed_attempts(jobs_engine: Engine) -> None:
    job = queue.enqueue("build", max_attempts=2, engine=jobs_engine)
    alive = queue.enqueue("build", engine=jobs_engine)
    first = queue.claim("crashed", engine=jobs_engine)
    healthy = queue.claim("healthy", engine=jobs_engine)
    assert first is not None and healthy is not None and healthy.id == alive.id
    assert queue.recover_expired(engine=jobs_engine) == []  # leases still valid
    age(jobs_engine, job.id, lease=True)
    assert queue.recover_expired(kinds=["eval"], engine=jobs_engine) == []
    [requeued] = queue.recover_expired(engine=jobs_engine)
    assert (requeued.id, requeued.status, requeued.attempts) == (job.id, "queued", 1)
    assert requeued.worker_id is None and requeued.lease_until is None
    assert requeued.error == "The worker stopped responding during attempt 1 (its lease ran out)."
    assert 5 < row(jobs_engine, job.id).due_in <= 10
    assert queue.get_job(alive.id, engine=jobs_engine).status == "running"  # type: ignore[union-attr]

    with pytest.raises(LeaseLost):  # the crashed worker can no longer touch the job
        queue.heartbeat(first.lease, engine=jobs_engine)
    for write in (
        lambda: queue.report(first.lease, stage="x", progress=0.5, engine=jobs_engine),
        lambda: queue.complete(first.lease, engine=jobs_engine),
        lambda: queue.fail(first.lease, "late", engine=jobs_engine),
        lambda: queue.release(first.lease, engine=jobs_engine),
    ):
        with pytest.raises(LeaseLost):
            write()

    age(jobs_engine, job.id, due=True)
    second = queue.claim("w2", engine=jobs_engine)
    assert second is not None and (second.id, second.attempts) == (job.id, 2)
    age(jobs_engine, job.id, lease=True)
    [failed] = queue.recover_expired(engine=jobs_engine)
    assert (failed.status, failed.attempts, failed.worker_id) == ("failed", 2, "w2")
    assert failed.finished_at is not None


def test_a_heartbeat_wins_over_recovery_of_a_live_lease(jobs_engine: Engine) -> None:
    job = queue.enqueue("build", engine=jobs_engine)
    claimed = queue.claim("w", engine=jobs_engine)
    assert claimed is not None
    age(jobs_engine, job.id, lease=True)
    queue.heartbeat(claimed.lease, engine=jobs_engine)  # just in time
    assert queue.recover_expired(engine=jobs_engine) == []


def test_release_gives_the_attempt_back_and_keeps_the_progress(jobs_engine: Engine) -> None:
    job = queue.enqueue("build", engine=jobs_engine)
    claimed = queue.claim("w", engine=jobs_engine)
    assert claimed is not None
    queue.report(claimed.lease, stage="parse", progress=0.4, engine=jobs_engine)
    released = queue.release(claimed.lease, engine=jobs_engine)
    assert (released.status, released.attempts, released.worker_id) == ("queued", 0, None)
    assert (released.stage, released.progress) == ("parse", 0.4)
    assert abs(row(jobs_engine, job.id).due_in) < 5
    again = queue.claim("w", engine=jobs_engine)
    assert again is not None and again.attempts == 1 and (again.stage, again.progress) == (None, 0)


def test_jobs_belong_to_snapshots(jobs_engine: Engine) -> None:
    with jobs_engine.begin() as connection:
        repo = connection.execute(
            text(
                "INSERT INTO repos (owner, name, default_branch) VALUES ('acme', 'jobs', 'main') "
                "RETURNING id"
            )
        ).scalar_one()
        snapshot = connection.execute(
            text(
                "INSERT INTO snapshots (repo_id, commit_sha, config_fingerprint) "
                "VALUES (:r, :sha, 'f') RETURNING id"
            ),
            {"r": repo, "sha": SHA},
        ).scalar_one()
    try:
        assert queue.latest_job(snapshot, engine=jobs_engine) is None
        older = queue.enqueue("build", snapshot_id=snapshot, engine=jobs_engine)
        newer = queue.enqueue("build", snapshot_id=snapshot, engine=jobs_engine)
        queue.enqueue("eval", snapshot_id=snapshot, engine=jobs_engine)
        latest = queue.latest_job(snapshot, kind="build", engine=jobs_engine)
        assert latest is not None and latest.id == newer.id != older.id
        with jobs_engine.begin() as connection:
            connection.execute(text("DELETE FROM snapshots WHERE id = :s"), {"s": snapshot})
        assert queue.get_job(older.id, engine=jobs_engine) is None  # deleted with the snapshot
    finally:
        with jobs_engine.begin() as connection:
            connection.execute(text("DELETE FROM repos WHERE id = :r"), {"r": repo})


def test_backoff_and_error_text() -> None:
    assert [backoff_delay(n) for n in (0, 1, 2, 3, 4, 9)] == [10, 10, 60, 300, 300, 300]
    assert backoff_delay(2, (1.5,)) == 1.5 and backoff_delay(1, ()) == 0
    assert error_text(KeyError("x")) == "KeyError: 'x'"
    assert error_text("  ") == "Unknown error."
    long = error_text("x" * 5000)
    assert len(long) == queue.MAX_ERROR_CHARS and long.endswith("…")
