"""The jobs table: indexes and constraints as the migration creates them."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from repoatlas.jobs.models import JOB_STATUSES


def _definitions(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'jobs'")
        ).all()
    return {name: definition for name, definition in rows}


def test_indexes(jobs_engine: Engine) -> None:
    found = _definitions(jobs_engine)
    assert set(found) == {
        "pk_jobs",
        "ix_jobs_claim",
        "ix_jobs_lease",
        "ix_jobs_snapshot_id",
        "uq_jobs_active_dedupe_key",
    }
    assert "(run_after, id) WHERE (status = 'queued'::text)" in found["ix_jobs_claim"]
    assert "(lease_until) WHERE (status = 'running'::text)" in found["ix_jobs_lease"]
    assert found["uq_jobs_active_dedupe_key"].startswith("CREATE UNIQUE INDEX")
    assert (
        "status = ANY (ARRAY['queued'::text, 'running'::text])"
        in (found["uq_jobs_active_dedupe_key"])
    )


def _rejects(engine: Engine, **values: Any) -> bool:
    columns = ", ".join(["kind", *values])
    params = ", ".join(["'build'", *(f":{name}" for name in values)])
    try:
        with engine.begin() as connection:
            connection.execute(text(f"INSERT INTO jobs ({columns}) VALUES ({params})"), values)
    except IntegrityError:
        return True
    return False


@pytest.mark.parametrize("status", JOB_STATUSES)
def test_every_status_is_allowed(jobs_engine: Engine, status: str) -> None:
    assert not _rejects(jobs_engine, status=status)


def test_constraints(jobs_engine: Engine) -> None:
    assert _rejects(jobs_engine, status="done")
    assert _rejects(jobs_engine, progress=1.5)
    assert _rejects(jobs_engine, progress=-0.1)
    assert _rejects(jobs_engine, attempts=-1)
    assert _rejects(jobs_engine, max_attempts=0)
    assert _rejects(jobs_engine, snapshot_id=999_999)  # snapshots must exist
