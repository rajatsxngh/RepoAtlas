"""Per-client rate limits: bucket arithmetic, client keys, groups, and 429s with Retry-After."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.api.ratelimit import Group, Limit, RateLimiter, RateLimitMiddleware, TokenBucket
from tests.unit.api.helpers import api_settings, client_for, error_of

WEB = "http://localhost:3000"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_a_bucket_refills_continuously_up_to_its_capacity() -> None:
    bucket = TokenBucket(capacity=5, rate=5 / 3600, tokens=5, updated=0.0)
    assert [bucket.take(0.0) for _ in range(5)] == [0.0] * 5
    assert bucket.take(0.0) == pytest.approx(720.0)  # one token every 12 minutes
    assert bucket.take(360.0) == pytest.approx(360.0)
    assert bucket.take(720.0) == 0.0
    assert bucket.take(10**9) == 0.0 and bucket.tokens == pytest.approx(4.0)  # capped at 5


def test_limits_are_per_group_and_per_client() -> None:
    clock = Clock()
    limiter = RateLimiter(
        {"build": Limit(2, 3600.0), "read": Limit(3, 60.0)}, salt=b"s", clock=clock
    )
    alice, bob = limiter.client_key("10.0.0.1"), limiter.client_key("10.0.0.2")
    assert [limiter.check("build", alice) for _ in range(2)] == [0.0, 0.0]
    assert limiter.check("build", alice) == pytest.approx(1800.0)
    assert limiter.check("read", alice) == 0.0  # reads have their own bucket
    assert limiter.check("build", bob) == 0.0  # and so does every client
    clock.now += 1800
    assert limiter.check("build", alice) == 0.0


def test_client_keys_hide_addresses_and_depend_on_the_salt() -> None:
    limits: dict[Group, Limit] = {"read": Limit(1, 60.0)}
    one, other = RateLimiter(limits, salt=b"one"), RateLimiter(limits, salt=b"other")
    key = one.client_key("203.0.113.9")
    assert "203.0.113.9" not in key and len(key) == 32
    assert key == one.client_key("203.0.113.9") != other.client_key("203.0.113.9")
    random_salts = RateLimiter(limits), RateLimiter(limits)
    assert random_salts[0].client_key("x") != random_salts[1].client_key("x")


def test_only_the_most_recent_clients_are_kept() -> None:
    limiter = RateLimiter({"read": Limit(1, 60.0)}, max_clients=3)
    for client in ("a", "b", "c", "d"):
        limiter.check("read", client)
    assert len(limiter) == 3
    assert limiter.check("read", "a") == 0.0  # forgotten, so it starts with a full bucket
    assert limiter.check("read", "d") > 0


def test_settings_configure_the_limits() -> None:
    limiter = RateLimiter.from_settings(
        api_settings(
            builds_per_hour=7,
            asks_per_hour=4,
            reads_per_minute=9,
            rate_limit_salt=SecretStr("pepper"),
        )
    )
    assert limiter.limits == {
        "build": Limit(7, 3600.0),
        "ask": Limit(4, 3600.0),
        "read": Limit(9, 60.0),
    }
    assert limiter.client_key("x") == RateLimiter(limiter.limits, salt=b"pepper").client_key("x")
    defaults = RateLimiter.from_settings(api_settings())
    assert defaults.limits == {
        "build": Limit(5, 3600.0),
        "ask": Limit(20, 3600.0),
        "read": Limit(120, 60.0),
    }
    with pytest.raises(ValueError):
        Limit(0, 60.0)


@pytest.mark.parametrize(
    ("method", "path", "group"),
    [
        ("POST", "/api/v1/repos", "build"),
        ("POST", "/api/v1/repos/", "build"),
        ("GET", "/api/v1/repos", "read"),
        ("POST", "/api/v1/snapshots/1/ask", "ask"),
        ("GET", "/api/v1/qa/4", "read"),
        ("GET", "/api/v1/snapshots/1/subgraph", "read"),
        ("GET", "/api/v1/jobs/3", "read"),
        ("GET", "/api/v1/nothing", "read"),
        ("GET", "/api/v1/health", None),
        ("GET", "/api/v1/ready", None),
        ("GET", "/api/v1/openapi.json", None),
        ("GET", "/api/v1/docs", None),
        ("OPTIONS", "/api/v1/repos", None),
        ("GET", "/elsewhere", None),
    ],
)
def test_requests_count_against_their_group(method: str, path: str, group: str | None) -> None:
    middleware = RateLimitMiddleware(
        lambda *_: None,  # type: ignore[arg-type]
        limiter=RateLimiter({"read": Limit(1, 1.0)}),
        prefix=API_PREFIX,
    )
    assert middleware.group(method, path) == group


@pytest.fixture
def app() -> Iterator[FastAPI]:
    app = create_app(api_settings(rate_limit_enabled=True, builds_per_hour=2, reads_per_minute=3))

    def echo() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_api_route(f"{API_PREFIX}/test/echo", echo, methods=["GET"])
    yield app
    app.state.engine.dispose()


@pytest.mark.anyio
async def test_clients_over_the_limit_get_429_with_retry_after(app: FastAPI) -> None:
    async with client_for(app, address="198.51.100.7") as client:
        allowed = [await client.get(f"{API_PREFIX}/test/echo") for _ in range(3)]
        limited = await client.get(f"{API_PREFIX}/test/echo", headers={"Origin": WEB})
        probe = await client.get(f"{API_PREFIX}/health")
        builds = [
            await client.post(f"{API_PREFIX}/repos", json={"url": "https://gitlab.com/a/b"})
            for _ in range(3)
        ]
    assert [response.status_code for response in allowed] == [200, 200, 200]
    assert limited.status_code == 429
    error = error_of(limited)
    assert error["code"] == "rate_limited"
    assert error["details"] == {"group": "read", "limit": 3, "period_s": 60.0, "retry_after_s": 20}
    assert "3 per minute" in error["message"]
    assert limited.headers["retry-after"] == "20"
    assert limited.headers["access-control-allow-origin"] == WEB  # the web app can read it
    assert "retry-after" in limited.headers["access-control-expose-headers"].lower()
    assert limited.headers["x-request-id"]
    assert probe.status_code == 200  # probes are never limited
    assert [response.status_code for response in builds] == [422, 422, 429]  # builds: 2 an hour
    assert builds[2].headers["retry-after"] == "1800"
    async with client_for(app, address="198.51.100.8") as other:
        assert (await other.get(f"{API_PREFIX}/test/echo")).status_code == 200


@pytest.mark.anyio
async def test_limits_can_be_turned_off() -> None:
    app = create_app(api_settings(rate_limit_enabled=False, reads_per_minute=1))
    app.add_api_route(f"{API_PREFIX}/test/echo", lambda: {"ok": "yes"}, methods=["GET"])
    try:
        async with client_for(app) as client:
            statuses = {(await client.get(f"{API_PREFIX}/test/echo")).status_code for _ in range(5)}
    finally:
        app.state.engine.dispose()
    assert statuses == {200} and app.state.rate_limiter is None


class Wall:
    """A wall clock: seconds since the epoch."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


DAY = 86_400.0
MORNING = 20_000 * DAY + 6 * 3600  # 06:00 UTC on some day


def test_a_daily_cap_counts_per_client_and_starts_again_at_midnight_utc() -> None:
    clock, wall = Clock(), Wall(MORNING)
    limiter = RateLimiter(
        {"ask": Limit(100, 3600.0), "read": Limit(100, 60.0)},
        salt=b"s",
        clock=clock,
        daily={"ask": 3},
        wall=wall,
    )
    alice, bob = limiter.client_key("10.0.0.1"), limiter.client_key("10.0.0.2")
    assert [limiter.check("ask", alice) for _ in range(3)] == [0.0, 0.0, 0.0]
    wait, limit = limiter.decide("ask", alice)
    assert wait == pytest.approx(18 * 3600) and limit == Limit(3, DAY)  # until midnight
    assert limiter.check("ask", bob) == 0.0  # per client
    assert all(limiter.check("read", alice) == 0.0 for _ in range(10))  # no daily cap on reads
    wall.now += 18 * 3600 - 1
    assert limiter.check("ask", alice) == pytest.approx(1.0)
    wall.now += 1
    assert limiter.check("ask", alice) == 0.0


def test_a_request_the_bucket_refuses_does_not_count_against_the_day() -> None:
    clock, wall = Clock(), Wall(MORNING)
    limiter = RateLimiter(
        {"build": Limit(1, 3600.0)}, salt=b"s", clock=clock, daily={"build": 2}, wall=wall
    )
    client = limiter.client_key("10.0.0.1")
    assert limiter.check("build", client) == 0.0
    wait, limit = limiter.decide("build", client)
    assert wait == pytest.approx(3600.0) and limit == Limit(1, 3600.0)  # the hourly bucket
    clock.now += 3600
    assert limiter.check("build", client) == 0.0  # the second of the day
    clock.now += 3600
    assert limiter.decide("build", client)[1] == Limit(2, DAY)
    clock.now += 3600  # the refused request above took no token either
    wall.now += DAY
    assert limiter.check("build", client) == 0.0


def test_zero_turns_a_daily_cap_off() -> None:
    limiter = RateLimiter({"ask": Limit(50, 3600.0)}, daily={"ask": 0, "build": 0})
    assert limiter.daily == {}
    assert all(limiter.check("ask", "c") == 0.0 for _ in range(50))


def test_settings_carry_the_daily_caps() -> None:
    limiter = RateLimiter.from_settings(api_settings())
    assert limiter.daily == {"build": 3, "ask": 20}
    assert RateLimiter.from_settings(api_settings(builds_per_day=0, asks_per_day=7)).daily == {
        "ask": 7
    }


@pytest.mark.anyio
async def test_the_daily_cap_answers_429_until_midnight() -> None:
    settings = api_settings(rate_limit_enabled=True, builds_per_hour=50, builds_per_day=2)
    app = create_app(settings)
    try:
        async with client_for(app) as client:
            for _ in range(2):  # the body is invalid, but the request still counts
                assert (await client.post(f"{API_PREFIX}/repos", json={})).status_code == 422
            refused = await client.post(f"{API_PREFIX}/repos", json={})
        async with client_for(app, address="198.51.100.7") as other:
            assert (await other.post(f"{API_PREFIX}/repos", json={})).status_code == 422
    finally:
        app.state.engine.dispose()
    assert refused.status_code == 429
    error = error_of(refused)
    assert error["code"] == "rate_limited" and "2 per day" in error["message"]
    assert error["details"]["period_s"] == DAY and error["details"]["limit"] == 2
    assert 1 <= int(refused.headers["Retry-After"]) <= DAY


@pytest.mark.anyio
async def test_behind_a_trusted_proxy_the_client_is_the_forwarded_address() -> None:
    def build(trust: bool) -> FastAPI:
        return create_app(
            api_settings(rate_limit_enabled=True, reads_per_minute=1, trust_proxy_headers=trust)
        )

    async def statuses(app: FastAPI) -> list[int]:
        found = []
        try:
            async with client_for(app) as client:  # every request comes from the proxy
                for caller in ("203.0.113.1", "203.0.113.2", "203.0.113.2"):
                    response = await client.get(
                        f"{API_PREFIX}/jobs/1", headers={"X-Forwarded-For": f"10.9.9.9, {caller}"}
                    )
                    found.append(response.status_code)
        finally:
            app.state.engine.dispose()
        return found

    assert [code == 429 for code in await statuses(build(True))] == [False, False, True]
    assert [code == 429 for code in await statuses(build(False))] == [False, True, True]
