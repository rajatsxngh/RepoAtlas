"""Liveness and readiness probes (API-8)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from repoatlas import __version__
from repoatlas.api.deps import EngineDep
from repoatlas.api.schemas import HealthStatus, ReadyStatus
from repoatlas.db import migrate

router = APIRouter(tags=["health"])


@lru_cache(maxsize=1)
def expected_revision() -> str | None:
    """The newest migration shipped with this code (what the database must be at)."""
    return migrate.head_revision()


@lru_cache(maxsize=1)
def known_revisions() -> frozenset[str]:
    return migrate.known_revisions()


@router.get("/health", summary="Liveness")
def health() -> HealthStatus:
    """The process is up. Checks nothing else, so it stays cheap for frequent probes."""
    return HealthStatus(version=__version__)


@router.get(
    "/ready",
    summary="Readiness",
    responses={503: {"model": ReadyStatus, "description": "Not ready"}},
)
def ready(engine: EngineDep, response: Response) -> ReadyStatus:
    """The database answers and is migrated to the revision this code expects (503 otherwise).

    A database at a revision this code doesn't know is ``ahead``: a newer release migrated it
    and this is the previous release, running again after a rollback. Migrations are backward
    compatible with the previous release (DEP-4), so that is ready too.
    """
    expected = expected_revision()
    try:
        with engine.connect() as connection:
            current = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except ProgrammingError:  # no alembic_version table: never migrated
        status = ReadyStatus(
            status="not_ready", database="not_migrated", revision=None, expected_revision=expected
        )
    except OperationalError:
        status = ReadyStatus(
            status="not_ready", database="unreachable", revision=None, expected_revision=expected
        )
    else:
        database: Literal["ok", "ahead", "not_migrated"]
        if current is not None and current == expected:
            database = "ok"
        elif current is not None and current not in known_revisions():
            database = "ahead"
        else:
            database = "not_migrated"
        status = ReadyStatus(
            status="not_ready" if database == "not_migrated" else "ready",
            database=database,
            revision=current,
            expected_revision=expected,
        )
    if status.status != "ready":
        response.status_code = 503
    return status
