"""Postgres fixtures: one throwaway database per test session, migrated to the latest revision.

The admin URL comes from ``REPOATLAS_TEST_DATABASE_URL`` if set, otherwise from the normal
settings (``REPOATLAS_DATABASE_URL`` or the nearest ``.env``). The test database is created
next to it and dropped when the session ends, so tests never touch real data.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from repoatlas.config import get_settings
from repoatlas.db import migrate

START_HINT = "Start it with: docker compose -f infra/docker-compose.yml up -d"


@pytest.fixture(scope="session")
def pg_admin_url() -> URL:
    """An existing database used only to create and drop the test database."""
    return make_url(os.environ.get("REPOATLAS_TEST_DATABASE_URL") or get_settings().database_url)


@pytest.fixture(scope="session")
def pg_url(pg_admin_url: URL) -> Iterator[str]:
    """URL of a fresh database migrated to head; dropped at the end of the session."""
    admin = create_engine(pg_admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError:
        admin.dispose()
        pytest.fail(
            f"Postgres is not reachable at {pg_admin_url.render_as_string(hide_password=True)}. "
            f"{START_HINT}",
            pytrace=False,
        )

    name = f"repoatlas_test_{uuid.uuid4().hex[:12]}"
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = pg_admin_url.set(database=name).render_as_string(hide_password=False)
    try:
        migrate.upgrade(url)
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def pg_engine(pg_url: str) -> Iterator[Engine]:
    engine = create_engine(pg_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(pg_engine: Engine) -> Iterator[Session]:
    """A session whose changes are rolled back after the test (its commits become savepoints)."""
    connection = pg_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
