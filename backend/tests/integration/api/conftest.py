"""API fixtures: an app on the test database, a fake GitHub with a local origin, a worker."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from repoatlas.api.app import create_app
from repoatlas.config import Settings
from repoatlas.jobs.worker import Worker
from tests.integration.origin import Origin, serve_origin
from tests.unit.api.helpers import api_settings


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    return serve_origin(tmp_path, monkeypatch)


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return api_settings(database_url=pg_url, data_dir=tmp_path / "data")


@pytest.fixture
def app(settings: Settings, clean_store: None) -> Iterator[FastAPI]:
    """The API on the test database; every snapshot and job it made is removed afterwards."""
    app = create_app(settings)
    yield app
    app.state.engine.dispose()


def run_worker(settings: Settings, engine: Engine, jobs: int = 1) -> None:
    """Process ``jobs`` due jobs with a worker that retries at once."""
    worker = Worker(settings, engine=engine, worker_id="test-worker", backoff_s=(0.0,))
    for _ in range(jobs):
        assert worker.run_once(), "no job was due"
