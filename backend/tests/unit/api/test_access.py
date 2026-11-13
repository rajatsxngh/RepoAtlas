"""The private access gate: a missing or wrong token is rejected; header and cookie both work."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from structlog.testing import capture_logs

from repoatlas.api.access import ACCESS_COOKIE, AccessGateMiddleware, cookie_value
from repoatlas.api.app import API_PREFIX, create_app
from tests.unit.api.helpers import api_settings, client_for, error_of

pytestmark = pytest.mark.anyio
TOKEN = "s3cret-token-for-tests"
WEB = "http://localhost:3000"
ECHO = f"{API_PREFIX}/test/echo"


def gated_app(**overrides: object) -> FastAPI:
    app = create_app(api_settings(access_token=SecretStr(TOKEN), **overrides))

    def echo() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_api_route(ECHO, echo, methods=["GET", "POST"])
    return app


@pytest.fixture
def app() -> Iterator[FastAPI]:
    app = gated_app()
    yield app
    app.state.engine.dispose()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_request_without_the_token_is_rejected(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get(ECHO)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    error = error_of(response)
    assert error["code"] == "unauthorized" and TOKEN not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        bearer("wrong"),
        bearer(TOKEN + "x"),
        bearer(TOKEN[:-1]),
        bearer(""),
        bearer(cookie_value(TOKEN)),  # the cookie's digest is not the token
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Cookie": f"{ACCESS_COOKIE}={TOKEN}"},  # the cookie holds the digest, not the token
        {"Cookie": f"{ACCESS_COOKIE}=wrong"},
        {"Cookie": f"other={cookie_value(TOKEN)}"},
        {"Cookie": "\x00=broken;;;"},
        {"X-Access-Token": TOKEN},
    ],
)
async def test_a_wrong_token_is_rejected(app: FastAPI, headers: dict[str, str]) -> None:
    async with client_for(app) as client:
        response = await client.get(ECHO, headers=headers)
        in_query = await client.get(ECHO, params={"access_token": TOKEN})
    assert response.status_code == 401 and error_of(response)["code"] == "unauthorized"
    assert in_query.status_code == 401  # never accepted in the URL


async def test_the_token_opens_the_api_as_a_header_or_a_cookie(app: FastAPI) -> None:
    async with client_for(app) as client:
        header = await client.get(ECHO, headers=bearer(TOKEN))
        lower = await client.get(ECHO, headers={"authorization": f"bearer {TOKEN}"})
        cookie = await client.post(
            ECHO, headers={"Cookie": f"theme=dark; {ACCESS_COOKIE}={cookie_value(TOKEN)}"}
        )
    assert [r.status_code for r in (header, lower, cookie)] == [200, 200, 200]
    assert header.json() == {"ok": "yes"}


async def test_every_route_is_gated_except_liveness_and_preflights(app: FastAPI) -> None:
    async with client_for(app) as client:
        health = await client.get(f"{API_PREFIX}/health")
        closed = [
            await client.get(f"{API_PREFIX}{path}")
            for path in ("/ready", "/openapi.json", "/docs", "/jobs/1", "/snapshots/1", "/nothing")
        ]
        build = await client.post(f"{API_PREFIX}/repos", json={"url": "https://github.com/a/b"})
        preflight = await client.options(
            ECHO,
            headers={
                "Origin": WEB,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        from_browser = await client.get(ECHO, headers={"Origin": WEB})
    assert health.status_code == 200
    assert {response.status_code for response in closed} == {401}
    assert build.status_code == 401
    assert preflight.status_code == 200
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
    # A browser can read the 401: the gate sits inside CORS.
    assert from_browser.status_code == 401
    assert from_browser.headers["access-control-allow-origin"] == WEB


async def test_rejected_requests_use_up_no_rate_limit_and_log_no_token() -> None:
    app = gated_app(rate_limit_enabled=True, reads_per_minute=1)
    try:
        with capture_logs() as logs:
            async with client_for(app) as client:
                rejected = [await client.get(ECHO, headers=bearer("guess")) for _ in range(3)]
                allowed = await client.get(ECHO, headers=bearer(TOKEN))
                limited = await client.get(ECHO, headers=bearer(TOKEN))
    finally:
        app.state.engine.dispose()
    assert {response.status_code for response in rejected} == {401}
    assert allowed.status_code == 200 and limited.status_code == 429
    assert TOKEN not in repr(logs) and "guess" not in repr(logs)


async def test_without_a_token_setting_the_api_is_open() -> None:
    app = create_app(api_settings())
    try:
        async with client_for(app) as client:
            response = await client.get(f"{API_PREFIX}/jobs/0")
    finally:
        app.state.engine.dispose()
    assert response.status_code != 401


def test_the_cookie_value_is_a_digest_and_an_empty_token_is_refused() -> None:
    value = cookie_value(TOKEN)
    assert TOKEN not in value and len(value) == 64 and value == cookie_value(TOKEN)
    assert value != cookie_value(TOKEN + "x")
    with pytest.raises(ValueError, match="empty"):
        AccessGateMiddleware(lambda *_: None, token=SecretStr(""), prefix=API_PREFIX)  # type: ignore[arg-type]


def test_the_token_setting_is_a_secret() -> None:
    settings = api_settings(access_token=SecretStr(TOKEN))
    assert TOKEN not in repr(settings) and TOKEN not in str(settings.model_dump())
