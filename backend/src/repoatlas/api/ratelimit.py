"""Per-client rate limits (API-6): token buckets kept in this process (one API instance in v1).

A client is the sha256 of its IP address with a salt, so addresses are never kept (NFR-9).
Build requests (``POST /repos``), questions (``POST /snapshots/{id}/ask``, which cost money),
and reads (every other route under ``/api/v1`` except the probes and the API docs) have separate
buckets: by default 5 builds and 20 questions per hour, and 120 reads per minute. A bucket
holds up to its limit and refills continuously; when it is empty the request
gets 429 ``rate_limited`` with ``Retry-After``, the seconds until a token is back.

Builds and questions also have a cap per client and UTC day (DEP-5; 3 builds and 20 questions
by default): a plain count that starts again at midnight UTC. Over it, the request gets the
same 429, with ``Retry-After`` the seconds until midnight. A request refused by either limit
uses up neither.

Behind a reverse proxy the client address is the proxy's. Either run uvicorn with
``--proxy-headers`` and ``--forwarded-allow-ips``, or set ``trust_proxy_headers``: the client
is then the last address in ``X-Forwarded-For``, the one the nearest proxy saw. Set it only
when the API is reachable through that proxy alone, since a direct caller could forge the header.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from repoatlas.api.errors import ErrorCode, error_response
from repoatlas.config import Settings

Group = Literal["build", "ask", "read"]
MAX_CLIENTS = 10_000  # buckets kept per group; the least recently seen are dropped first
DAY_S = 86_400.0


@dataclass(frozen=True)
class Limit:
    requests: int
    per_s: float

    def __post_init__(self) -> None:
        if self.requests < 1 or self.per_s <= 0:
            raise ValueError("A rate limit needs at least one request per positive period.")

    @property
    def rate(self) -> float:
        """Tokens added per second."""
        return self.requests / self.per_s


@dataclass
class TokenBucket:
    capacity: float
    rate: float
    tokens: float
    updated: float

    def take(self, now: float) -> float:
        """Take a token: 0 when one was there, else the seconds until one will be."""
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.rate


class RateLimiter:
    """Token buckets per (group, client), thread-safe, with a cap on how many are kept."""

    def __init__(
        self,
        limits: Mapping[Group, Limit],
        *,
        salt: bytes | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = MAX_CLIENTS,
        daily: Mapping[Group, int] | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.limits = dict(limits)
        self.daily = {group: cap for group, cap in (daily or {}).items() if cap > 0}
        self._wall = wall
        self._day = -1  # days since the epoch (UTC) that ``_counts`` belongs to
        self._counts: OrderedDict[tuple[Group, str], int] = OrderedDict()
        self._salt = salt if salt is not None else secrets.token_bytes(16)
        self._clock = clock
        self._max = max(1, max_clients) * max(1, len(self.limits))
        self._buckets: OrderedDict[tuple[Group, str], TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> RateLimiter:
        salt = settings.rate_limit_salt
        return cls(
            {
                "build": Limit(settings.builds_per_hour, 3600.0),
                "ask": Limit(settings.asks_per_hour, 3600.0),
                "read": Limit(settings.reads_per_minute, 60.0),
            },
            salt=salt.get_secret_value().encode("utf-8") if salt is not None else None,
            daily={"build": settings.builds_per_day, "ask": settings.asks_per_day},
        )

    def client_key(self, address: str) -> str:
        """A stable, unguessable name for a client address (the address itself is dropped)."""
        return hashlib.sha256(self._salt + address.encode("utf-8")).hexdigest()[:32]

    def check(self, group: Group, client: str) -> float:
        """Spend one request of ``client`` in ``group``: 0 when allowed, else seconds to wait."""
        return self.decide(group, client)[0]

    def decide(self, group: Group, client: str) -> tuple[float, Limit]:
        """Like ``check``, with the limit that applied: the daily cap when it refused the
        request, else the group's bucket."""
        limit = self.limits[group]
        key = (group, client)
        with self._lock:
            cap = self.daily.get(group)
            if cap is not None:
                wall = self._wall()
                day = int(wall // DAY_S)
                if day != self._day:
                    self._day = day
                    self._counts.clear()
                if self._counts.get(key, 0) >= cap:
                    return (day + 1) * DAY_S - wall, Limit(cap, DAY_S)
            wait = self._take(key, limit)
            if wait <= 0 and cap is not None:
                self._counts[key] = self._counts.get(key, 0) + 1
                self._counts.move_to_end(key)
                while len(self._counts) > self._max:
                    self._counts.popitem(last=False)
            return wait, limit

    def _take(self, key: tuple[Group, str], limit: Limit) -> float:
        """Take a token from the key's bucket (the caller holds the lock)."""
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(limit.requests, limit.rate, float(limit.requests), now)
            self._buckets[key] = bucket
            while len(self._buckets) > self._max:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(key)
        return bucket.take(now)

    def __len__(self) -> int:
        return len(self._buckets)


class RateLimitMiddleware:
    """Answer 429 with ``Retry-After`` once a client has used up its requests of a group."""

    def __init__(
        self, app: ASGIApp, *, limiter: RateLimiter, prefix: str, trust_proxy: bool = False
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.prefix = prefix
        self.trust_proxy = trust_proxy
        self._exempt = {f"{prefix}/{name}" for name in ("health", "ready", "openapi.json")}

    def group(self, method: str, path: str) -> Group | None:
        """Which limit a request counts against (``None``: not limited)."""
        if method == "OPTIONS" or not path.startswith(self.prefix + "/"):
            return None
        if path in self._exempt or path.startswith(f"{self.prefix}/docs"):
            return None
        if method == "POST" and path.rstrip("/") == f"{self.prefix}/repos":
            return "build"
        if method == "POST" and path.rstrip("/").endswith("/ask"):
            return "ask"
        return "read"

    def address(self, scope: Scope) -> str:
        """The client's address: the connection's, or behind a trusted proxy the last entry of
        ``X-Forwarded-For`` (what that proxy saw; earlier entries are the caller's to forge)."""
        if self.trust_proxy:
            forwarded = Headers(scope=scope).get("x-forwarded-for", "").split(",")[-1].strip()
            if forwarded:
                return forwarded[:64]
        client = scope.get("client")
        return str(client[0]) if client else "unknown"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        group = self.group(str(scope.get("method", "")), str(scope.get("path", "")))
        if group is None:
            await self.app(scope, receive, send)
            return
        wait, limit = self.limiter.decide(group, self.limiter.client_key(self.address(scope)))
        if wait <= 0:
            await self.app(scope, receive, send)
            return
        retry_after = max(1, math.ceil(wait))
        noun = "build requests" if group == "build" else "requests"
        response = error_response(
            429,
            ErrorCode.RATE_LIMITED,
            f"Too many {noun}: the limit is {limit.requests} per {_period(limit.per_s)}. "
            f"Try again in {retry_after} s.",
            details={
                "group": group,
                "limit": limit.requests,
                "period_s": limit.per_s,
                "retry_after_s": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)


def _period(seconds: float) -> str:
    named = {60.0: "minute", 3600.0: "hour", 86400.0: "day"}
    return named.get(seconds, f"{seconds:g} s")
