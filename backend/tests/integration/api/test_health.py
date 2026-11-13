"""Readiness against real databases: migrated, never migrated, and unreachable."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.db import migrate
from tests.unit.api.helpers import NOWHERE_DB, api_settings, client_for

pytestmark = pytest.mark.anyio


async def test_ready_when_the_database_is_migrated(pg_url: str) -> None:
    app = create_app(api_settings(database_url=pg_url))
    try:
        async with client_for(app) as client:
            response = await client.get(f"{API_PREFIX}/ready")
    finally:
        app.state.engine.dispose()
    assert response.status_code == 200
    head = migrate.head_revision()
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "revision": head,
        "expected_revision": head,
    }


async def test_not_ready_before_migrations(scratch_db_url: str) -> None:
    app = create_app(api_settings(database_url=scratch_db_url))
    try:
        async with client_for(app) as client:
            response = await client.get(f"{API_PREFIX}/ready")
        migrate.upgrade(scratch_db_url, "0001_baseline")
        async with client_for(app) as client:
            behind = await client.get(f"{API_PREFIX}/ready")
    finally:
        app.state.engine.dispose()
    assert response.status_code == 503
    assert response.json()["database"] == "not_migrated"
    assert response.json()["revision"] is None
    assert behind.status_code == 503
    assert behind.json()["revision"] == "0001_baseline"
    assert behind.json()["database"] == "not_migrated"


async def test_ready_on_a_database_a_newer_release_migrated(scratch_db_url: str) -> None:
    """After a rollback the previous release runs on the newer schema (DEP-4)."""
    migrate.upgrade(scratch_db_url)
    engine = create_engine(scratch_db_url)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '9999_from_the_future'"))
    engine.dispose()
    app = create_app(api_settings(database_url=scratch_db_url))
    try:
        async with client_for(app) as client:
            response = await client.get(f"{API_PREFIX}/ready")
    finally:
        app.state.engine.dispose()
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ahead",
        "revision": "9999_from_the_future",
        "expected_revision": migrate.head_revision(),
    }


async def test_not_ready_when_the_database_is_unreachable() -> None:
    app = create_app(api_settings(database_url=NOWHERE_DB))
    try:
        async with client_for(app) as client:
            response = await client.get(f"{API_PREFIX}/ready")
            health = await client.get(f"{API_PREFIX}/health")
    finally:
        app.state.engine.dispose()
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database": "unreachable",
        "revision": None,
        "expected_revision": migrate.head_revision(),
    }
    assert health.status_code == 200
