"""Per-client rate limits (API-6): token buckets kept in this process (one API instance in v1).

A client is the sha256 of its IP address with a salt, so addresses are never kept (NFR-9).
Build requests (``POST /repos``) and reads (every other route under ``/api/v1`` except the
probes and the API docs) have separate buckets: by default 5 builds per hour and 120 reads per
minute. A bucket holds up to its limit and refills continuously; when it is empty the request
gets 429 ``rate_limited`` with ``Retry-After``, the seconds until a token is back.

Behind a reverse proxy, run uvicorn with ``--proxy-headers`` and ``--forwarded-allow-ips`` so
the client address is the caller's, not the proxy's.
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

from starlette.types import ASGIApp, Receive, Scope, Send

from repoatlas.api.errors import ErrorCode, error_response
from repoatlas.config import Settings

Group = Literal["build", "read"]
MAX_CLIENTS = 10_000  # buckets kept per group; the least recently seen are dropped first


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
    ) -> None:
        self.limits = dict(limits)
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
                "read": Limit(settings.reads_per_minute, 60.0),
            },
            salt=salt.get_secret_value().encode("utf-8") if salt is not None else None,
        )

    def client_key(self, address: str) -> str:
        """A stable, unguessable name for a client address (the address itself is dropped)."""
        return hashlib.sha256(self._salt + address.encode("utf-8")).hexdigest()[:32]

    def check(self, group: Group, client: str) -> float:
        """Spend one request of ``client`` in ``group``: 0 when allowed, else seconds to wait."""
        limit = self.limits[group]
        with self._lock:
            now = self._clock()
            key = (group, client)
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

    def __init__(self, app: ASGIApp, *, limiter: RateLimiter, prefix: str) -> None:
        self.app = app
        self.limiter = limiter
        self.prefix = prefix
        self._exempt = {f"{prefix}/{name}" for name in ("health", "ready", "openapi.json")}

    def group(self, method: str, path: str) -> Group | None:
        """Which limit a request counts against (``None``: not limited)."""
        if method == "OPTIONS" or not path.startswith(self.prefix + "/"):
            return None
        if path in self._exempt or path.startswith(f"{self.prefix}/docs"):
            return None
        if method == "POST" and path.rstrip("/") == f"{self.prefix}/repos":
            return "build"
        return "read"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        group = self.group(str(scope.get("method", "")), str(scope.get("path", "")))
        if group is None:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        address = str(client[0]) if client else "unknown"
        wait = self.limiter.check(group, self.limiter.client_key(address))
        if wait <= 0:
            await self.app(scope, receive, send)
            return
        retry_after = max(1, math.ceil(wait))
        limit = self.limiter.limits[group]
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
