"""The gate in front of the MCP HTTP transport (AGT-7): a bearer token, then a rate limit."""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any

import pytest

from repoatlas.agent.mcp_server import MAX_CLIENTS, Gate

pytestmark = pytest.mark.anyio
TOKEN = "s3cret-token-for-the-gate-test"  # a made-up value


class Inner:
    """An ASGI app that records what reached it and answers 200."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


async def request(
    gate: Gate, authorization: str | None = None, address: str = "10.0.0.1"
) -> tuple[int, dict[str, str], Any]:
    headers = [(b"host", b"x")]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {"type": "http", "method": "POST", "headers": headers, "client": (address, 5000)}
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await gate(scope, receive, send)
    start, body = sent[0], sent[1]["body"]
    reply = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], reply, json.loads(body) if body.startswith(b"{") else body


def gate(inner: Inner, clock: Clock | None = None, per_minute: int = 2) -> Gate:
    return Gate(inner, TOKEN, calls_per_minute=per_minute, clock=clock or Clock())


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer wrong",
        f"Bearer {TOKEN}x",
        f"Bearer {TOKEN[:-1]}",
        f"Basic {TOKEN}",
        f"Token {TOKEN}",
        TOKEN,
    ],
)
async def test_a_request_without_the_exact_token_never_reaches_the_server(authorization):
    inner = Inner()
    status, headers, body = await request(gate(inner), authorization)
    assert status == 401 and inner.calls == []
    assert headers["www-authenticate"] == 'Bearer realm="repoatlas-mcp"'
    assert body["error"]["code"] == "unauthorized"
    assert TOKEN not in json.dumps(body) and TOKEN not in json.dumps(headers)


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
async def test_the_token_lets_a_request_through(scheme):
    inner = Inner()
    status, _, body = await request(gate(inner), f"{scheme} {TOKEN}")
    assert (status, body, inner.calls) == (200, b"ok", ["http"])


async def test_each_client_gets_its_calls_per_minute_and_they_come_back():
    inner, clock = Inner(), Clock()
    limited = gate(inner, clock, per_minute=2)
    good = f"Bearer {TOKEN}"
    assert [(await request(limited, good))[0] for _ in range(3)] == [200, 200, 429]
    status, headers, body = await request(limited, good)
    assert status == 429 and body["error"]["code"] == "rate_limited"
    assert headers["retry-after"] == "30"  # two a minute: one comes back every 30 seconds
    # Another client has its own allowance.
    assert (await request(limited, good, address="10.0.0.2"))[0] == 200
    # Unauthenticated requests are refused first and use up nothing.
    assert (await request(limited, None, address="10.0.0.3"))[0] == 401
    assert (await request(limited, good, address="10.0.0.3"))[0] == 200
    clock.now += 30
    assert (await request(limited, good))[0] == 200
    assert (await request(limited, good))[0] == 429
    assert len(inner.calls) == 5


async def test_the_gate_keeps_a_bounded_number_of_clients():
    inner = Inner()
    limited = gate(inner, per_minute=1)
    for index in range(MAX_CLIENTS + 50):
        await request(limited, f"Bearer {TOKEN}", address=f"10.1.{index // 250}.{index % 250}")
    assert len(limited._buckets) == MAX_CLIENTS


async def test_lifespan_events_pass_through_and_a_gate_needs_a_token():
    inner = Inner()
    limited = gate(inner)

    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: MutableMapping[str, Any]) -> None:
        return None

    await limited({"type": "lifespan"}, receive, send)
    assert inner.calls == ["lifespan"]
    with pytest.raises(ValueError, match="needs a bearer token"):
        Gate(inner, "", calls_per_minute=10)
    with pytest.raises(ValueError, match="at least one call"):
        Gate(inner, TOKEN, calls_per_minute=0)
