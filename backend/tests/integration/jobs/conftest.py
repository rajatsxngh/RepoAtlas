"""Job queue fixtures: an emptied jobs table, and helpers to read or age jobs directly."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text


def empty_jobs(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE jobs RESTART IDENTITY"))


@pytest.fixture
def jobs_engine(pg_engine: Engine) -> Iterator[Engine]:
    """The test database, with the jobs table emptied after each test."""
    yield pg_engine
    empty_jobs(pg_engine)


def row(engine: Engine, job_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT *, extract(epoch FROM run_after - now()) AS due_in, "
                "extract(epoch FROM lease_until - now()) AS lease_left FROM jobs WHERE id = :id"
            ),
            {"id": job_id},
        ).one()


def age(engine: Engine, job_id: int, *, lease: bool = False, due: bool = False) -> None:
    """Pretend time passed: end the job's lease and/or make it due now."""
    changes = []
    if lease:
        changes.append("lease_until = now() - interval '1 second'")
    if due:
        changes.append("run_after = now() - interval '1 second'")
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE jobs SET {', '.join(changes)} WHERE id = :id"), {"id": job_id}
        )
