"""Shared pytest configuration.

Tests under ``tests/unit`` are marked ``unit`` and tests under ``tests/integration`` are marked
``integration`` automatically, so ``pytest -m unit`` selects the tests that need no services.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        parts = item.path.parts
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "unit" in parts:
            item.add_marker(pytest.mark.unit)
