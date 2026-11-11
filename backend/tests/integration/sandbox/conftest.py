"""Sandbox tests need a Docker daemon; they skip without one, and in CI unless opted in.

Set ``REPOATLAS_SANDBOX_TESTS=1`` to require them (they then fail instead of skipping), or
``REPOATLAS_SANDBOX_TESTS=0`` to skip them. Images are built once and reused across runs.
"""

from __future__ import annotations

import os

import pytest

from repoatlas.sandbox.docker import docker_available

_SETTING = os.environ.get("REPOATLAS_SANDBOX_TESTS")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    if _SETTING == "1":
        return
    if _SETTING == "0":
        reason = "REPOATLAS_SANDBOX_TESTS=0"
    elif os.environ.get("CI"):
        reason = "sandbox tests build images; set REPOATLAS_SANDBOX_TESTS=1 to run them in CI"
    elif not docker_available():
        reason = "Docker is not available"
    else:
        return
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        if "sandbox" in item.path.parts and "integration" in item.path.parts:
            item.add_marker(marker)
