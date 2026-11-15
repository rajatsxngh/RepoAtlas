"""The MCP server over its real transports (AGT-7): stdio as a subprocess started by the
``repoatlas mcp`` command, and Streamable HTTP behind the bearer token and the rate limit."""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from typer.testing import CliRunner

from repoatlas.agent.mcp_server import MCP_PATH, http_app
from repoatlas.agent.tools import TOOL_NAMES
from repoatlas.cli import app as cli
from repoatlas.config import get_settings
from tests.integration.agent.conftest import Built
from tests.integration.mcp.test_mcp_server import text_of

pytestmark = pytest.mark.anyio
TOKEN = "test-token-for-the-mcp-gate"  # a made-up value for this test server
CART = "src/shop/cart.py"


# --- stdio ---------------------------------------------------------------------------------


async def test_the_command_serves_the_tools_over_stdio(shop: Built, tmp_path: Path):
    """A real subprocess: ``repoatlas mcp --snapshot <id>`` talking over stdin and stdout."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from repoatlas.cli import app; app()",
            "mcp",
            "--snapshot",
            str(shop.snapshot_id),
        ],
        env={
            "REPOATLAS_DATABASE_URL": shop.settings.database_url,
            "REPOATLAS_EMBEDDING_MODEL": "hash-256",
            "REPOATLAS_DATA_DIR": str(tmp_path / "data"),
            "REPOATLAS_LOG_LEVEL": "WARNING",
        },
        cwd=tmp_path,  # no .env above it: the server runs on these variables alone
    )
    async with Client(parameters) as client:
        listed = await client.list_tools()
        found = await client.call_tool("find_definition", {"name": "Cart"})
        code = await client.call_tool("read_file", {"path": CART, "start": 8, "end": 9})
        refused = await client.call_tool("read_file", {"path": "../x", "start": 1, "end": 2})
    assert [tool.name for tool in listed.tools] == list(TOOL_NAMES)
    assert "py:src/shop/cart.py:Cart:class" in text_of(found)
    assert "8 | class Cart:" in text_of(code)
    assert refused.is_error and "is not a file of this snapshot" in text_of(refused)


# --- the command's checks ------------------------------------------------------------------


@pytest.fixture
def settings_for(shop: Built, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", shop.settings.database_url)
    monkeypatch.delenv("REPOATLAS_MCP_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_http_refuses_to_start_without_a_token(shop: Built, settings_for: None):
    arguments = ["mcp", "--snapshot", str(shop.snapshot_id), "--transport", "http"]
    result = CliRunner().invoke(cli, arguments)
    assert result.exit_code == 2
    assert "needs a token: set REPOATLAS_MCP_TOKEN" in result.output


def test_the_command_rejects_unknown_transports_and_snapshots(shop: Built, settings_for: None):
    bad = CliRunner().invoke(cli, ["mcp", "--snapshot", "1", "--transport", "carrier-pigeon"])
    assert bad.exit_code == 2 and "stdio or http" in bad.output
    missing = CliRunner().invoke(cli, ["mcp", "--snapshot", str(shop.snapshot_id + 999)])
    assert missing.exit_code == 1 and "does not exist" in missing.output


# --- Streamable HTTP -------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def served(server: Server[Any]) -> Iterator[str]:
    """The server on a local port, behind the gate; three calls a minute per client."""
    port = free_port()
    asgi = http_app(server, TOKEN, calls_per_minute=3)
    config = uvicorn.Config(asgi, host="127.0.0.1", port=port, log_level="warning")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not running.started:
        assert time.monotonic() < deadline, "the MCP server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}{MCP_PATH}"
    running.should_exit = True
    thread.join(timeout=10)


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {},
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def test_http_needs_the_bearer_token(served: str):
    async with httpx2.AsyncClient() as http:
        anonymous = await http.post(served, json=INITIALIZE, headers=HEADERS)
        wrong = await http.post(
            served, json=INITIALIZE, headers={**HEADERS, "Authorization": "Bearer nope"}
        )
        basic = await http.post(
            served, json=INITIALIZE, headers={**HEADERS, "Authorization": f"Basic {TOKEN}"}
        )
        listing = await http.get(served)
    for response in (anonymous, wrong, basic, listing):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == 'Bearer realm="repoatlas-mcp"'
        assert response.json()["error"]["code"] == "unauthorized"
        assert TOKEN not in response.text


async def test_an_mcp_client_with_the_token_calls_tools_over_http(served: str):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with (
        httpx2.AsyncClient(headers=headers) as http,
        Client(streamable_http_client(served, http_client=http)) as client,
    ):
        listed = await client.list_tools()
        found = await client.call_tool("find_definition", {"name": "total"})
    assert [tool.name for tool in listed.tools] == list(TOOL_NAMES)
    assert found.structured_content["evidence"][0]["path"] == CART


async def test_http_is_rate_limited_per_client(served: str):
    headers = {**HEADERS, "Authorization": f"Bearer {TOKEN}"}
    async with httpx2.AsyncClient() as http:
        statuses = []
        for _ in range(5):
            response = await http.post(served, json=INITIALIZE, headers=headers)
            statuses.append(response.status_code)
        # A request without the token is turned away before it can use up the limit.
        anonymous = await http.post(served, json=INITIALIZE, headers=HEADERS)
    assert statuses[:3] == [statuses[0]] * 3 and statuses[0] != 429
    assert statuses[3:] == [429, 429]
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1
    assert anonymous.status_code == 401
