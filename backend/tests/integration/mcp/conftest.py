"""MCP fixtures: the built ``py_small`` snapshot of the agent tests, and a server over it."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server import Server

from repoatlas.agent.mcp_server import build_server
from repoatlas.answers.validate import StoreBlobs
from tests.integration.agent.conftest import Built, agent_settings, shop

__all__ = ["agent_settings", "shop"]


@pytest.fixture
def server(shop: Built) -> Server[Any]:
    return build_server(shop.toolbox(), StoreBlobs(shop.engine), result_tokens=2_000)
