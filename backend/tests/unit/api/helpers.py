"""Shared helpers for API tests: settings that ignore ``.env``, and an in-process HTTP client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from repoatlas.config import Settings

# A database URL nothing listens on: creating an engine never connects, so apps whose tests
# don't touch the database can use it.
NOWHERE_DB = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere"
BASE_URL = "http://testserver"


def api_settings(**overrides: Any) -> Settings:
    """Settings for API tests, independent of the developer's environment and ``.env``."""
    values: dict[str, Any] = {
        "database_url": NOWHERE_DB,
        "data_dir": Path(".repoatlas-test"),
        "db_connect_timeout_s": 1.0,
        "rate_limit_enabled": False,  # the rate-limit tests turn it on
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@asynccontextmanager
async def client_for(
    app: FastAPI, *, address: str = "127.0.0.1"
) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client that calls ``app`` in process from ``address``. Unexpected exceptions
    become responses (as they would for a real client) instead of being raised into the
    test."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(address, 50000))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


def error_of(response: httpx.Response) -> dict[str, Any]:
    """The ``error`` object of an error response, after checking the model's shape."""
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == {"code", "message", "details"}, error
    assert isinstance(error["message"], str) and error["message"], error
    assert isinstance(error["details"], dict), error
    return dict(error)
