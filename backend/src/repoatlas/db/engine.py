"""SQLAlchemy engine creation."""

from __future__ import annotations

import math
from functools import lru_cache

from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.exc import ArgumentError

from repoatlas.config import get_settings


def create_db_engine(
    database_url: str, *, echo: bool = False, connect_timeout_s: float | None = None
) -> Engine:
    """Create an engine for ``database_url`` (connections are checked before use).

    ``connect_timeout_s`` bounds how long opening one connection may take (libpq counts whole
    seconds); without it, a host that drops packets can block a caller for minutes.
    """
    connect_args: dict[str, object] = {}
    if connect_timeout_s is not None:
        connect_args["connect_timeout"] = max(1, math.ceil(connect_timeout_s))
    return create_engine(database_url, pool_pre_ping=True, echo=echo, connect_args=connect_args)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine for the configured database."""
    return create_db_engine(get_settings().database_url)


def redact_url(database_url: str) -> str:
    """Return ``database_url`` with the password hidden, safe for logs and error messages."""
    try:
        return make_url(database_url).render_as_string(hide_password=True)
    except ArgumentError:
        return "<invalid database URL>"
