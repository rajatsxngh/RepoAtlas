"""Store test fixtures: an empty store per test, and the fixture repos' graphs."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from repoatlas.store.models import STORE_TABLES
from tests.integration.store.helpers import Built, build_fixture


@pytest.fixture(autouse=True)
def clean_store(pg_engine: Engine) -> Iterator[None]:
    """Store tests commit (the loader owns its transactions), so empty the tables afterwards."""
    yield
    with pg_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(STORE_TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def py_small() -> Built:
    return build_fixture("py_small")


@pytest.fixture(scope="session")
def ts_small() -> Built:
    return build_fixture("ts_small")
