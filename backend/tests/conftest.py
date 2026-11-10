"""Shared pytest configuration.

Tests under ``tests/unit`` are marked ``unit`` and tests under ``tests/integration`` are marked
``integration`` automatically, so ``pytest -m unit`` selects the tests that need no services.
Embeddings use the hash embedder unless ``REPOATLAS_TEST_EMBEDDING_MODEL`` names a model.
"""

from __future__ import annotations

import os

import pytest

# Tests embed with the deterministic hash embedder, so they never download a model. Set
# REPOATLAS_TEST_EMBEDDING_MODEL to run them with a real one.
TEST_EMBEDDING_MODEL = os.environ.get("REPOATLAS_TEST_EMBEDDING_MODEL", "hash-256")
os.environ["REPOATLAS_EMBEDDING_MODEL"] = TEST_EMBEDDING_MODEL


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        parts = item.path.parts
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "unit" in parts:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (``pytest.mark.anyio``) run on asyncio, like uvicorn."""
    return "asyncio"
