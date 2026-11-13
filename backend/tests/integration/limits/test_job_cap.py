"""The cap on jobs running at once, across workers (DEP-5)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy import Engine

from repoatlas.config import Settings
from repoatlas.jobs.queue import claim, complete, enqueue
from repoatlas.jobs.worker import JobContext, JobHandler, Worker
from tests.integration.jobs.conftest import empty_jobs, row


@pytest.fixture
def jobs_engine(pg_engine: Engine) -> Engine:
    empty_jobs(pg_engine)
    return pg_engine


def test_nothing_is_claimed_while_the_cap_is_reached(jobs_engine: Engine) -> None:
    jobs = [enqueue("build", {"n": n}, engine=jobs_engine) for n in range(3)]
    first = claim("w1", max_running=2, engine=jobs_engine)
    second = claim("w2", max_running=2, engine=jobs_engine)
    assert first is not None and second is not None
    assert claim("w3", max_running=2, engine=jobs_engine) is None
    assert row(jobs_engine, jobs[2].id).status == "queued"
    complete(first.lease, engine=jobs_engine)
    third = claim("w3", max_running=2, engine=jobs_engine)
    assert third is not None and third.id == jobs[2].id


def test_the_cap_counts_only_the_claimed_kinds(jobs_engine: Engine) -> None:
    enqueue("build", engine=jobs_engine)
    enqueue("other", engine=jobs_engine)
    assert claim("w1", kinds=["other"], max_running=1, engine=jobs_engine) is not None
    assert claim("w2", kinds=["build"], max_running=1, engine=jobs_engine) is not None
    enqueue("build", {"n": 2}, engine=jobs_engine)
    assert claim("w3", kinds=["build"], max_running=1, engine=jobs_engine) is None


def test_without_a_cap_every_due_job_can_run(jobs_engine: Engine) -> None:
    for n in range(4):
        enqueue("build", {"n": n}, engine=jobs_engine)
    assert all(claim(f"w{n}", engine=jobs_engine) is not None for n in range(4))
    with pytest.raises(ValueError, match="max_running"):
        claim("w", max_running=0, engine=jobs_engine)


def test_racing_claims_never_exceed_the_cap(jobs_engine: Engine) -> None:
    for n in range(8):
        enqueue("build", {"n": n}, engine=jobs_engine)
    barrier = threading.Barrier(8)
    claimed: list[int] = []

    def racer(n: int) -> None:
        barrier.wait()
        job = claim(f"w{n}", max_running=2, engine=jobs_engine)
        if job is not None:
            claimed.append(job.id)

    threads = [threading.Thread(target=racer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == 2


def test_a_worker_waits_for_a_free_place(jobs_engine: Engine, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, job_max_running=1)
    ran: list[int] = []

    def handle(ctx: JobContext) -> None:
        ran.append(ctx.job.id)

    handlers = {"build": JobHandler("build", handle)}
    worker = Worker(settings, engine=jobs_engine, handlers=handlers, worker_id="capped")
    held = enqueue("build", {"n": 1}, engine=jobs_engine)
    waiting = enqueue("build", {"n": 2}, engine=jobs_engine)
    other = claim("elsewhere", engine=jobs_engine)  # another worker runs the first job
    assert other is not None and other.id == held.id
    assert worker.run_once() is False and ran == []
    complete(other.lease, engine=jobs_engine)
    assert worker.run_once() is True
    assert ran == [waiting.id]

    unlimited = Settings(_env_file=None, data_dir=tmp_path, job_max_running=0)
    assert Worker(unlimited, engine=jobs_engine, handlers=handlers).max_running is None
