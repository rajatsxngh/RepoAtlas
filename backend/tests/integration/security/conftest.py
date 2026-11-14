"""Fixtures for the security tests: the API on the test database."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from repoatlas.api.app import create_app
from repoatlas.config import Settings
from tests.unit.api.helpers import api_settings


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return api_settings(database_url=pg_url, data_dir=tmp_path / "data")


@pytest.fixture
def app(settings: Settings, clean_store: None) -> Iterator[FastAPI]:
    """The API on the test database; every snapshot it stored is removed afterwards."""
    app = create_app(settings)
    yield app
    app.state.engine.dispose()
