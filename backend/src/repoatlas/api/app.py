"""The FastAPI application (API-1).

``create_app(settings)`` builds one app with its own database engine and graph store (the store
owns the snapshot adjacency cache, so one app should serve every request of a process). Run it
with ``uvicorn repoatlas.api.app:create_app --factory``.

Middleware, outermost first: a request ID for every request (echoed in ``X-Request-ID`` and
bound to the log context), CORS for the web app's origins, a last-resort handler that turns
unexpected exceptions into a 500 in the error model, per-client rate limits (inside CORS, so a
browser can read a 429's ``Retry-After``), and the symbol-key path fix. Routes run as plain
functions in the threadpool, because the store and the GitHub client are synchronous.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from repoatlas import __version__
from repoatlas.api import errors
from repoatlas.api.middleware import (
    REQUEST_ID_HEADER,
    EncodedKeyMiddleware,
    RequestContextMiddleware,
    UnhandledErrorMiddleware,
)
from repoatlas.api.ratelimit import RateLimiter, RateLimitMiddleware
from repoatlas.api.routes import health, jobs, repos, snapshots
from repoatlas.config import Settings, get_settings
from repoatlas.db.engine import create_db_engine
from repoatlas.logging import configure
from repoatlas.store.graphstore import GraphStore

API_PREFIX = "/api/v1"
DESCRIPTION = (
    "Code maps of public GitHub repositories: build a snapshot, follow its progress, and read "
    "its graph and code. Every error has the shape "
    '`{"error": {"code", "message", "details"}}` with a stable `code`.'
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API app for ``settings`` (default: the process settings)."""
    settings = settings or get_settings()
    if not structlog.is_configured():
        configure(settings)
    engine = create_db_engine(
        settings.database_url, connect_timeout_s=settings.db_connect_timeout_s
    )

    limiter = RateLimiter.from_settings(settings) if settings.rate_limit_enabled else None
    limits = (
        []
        if limiter is None
        else [Middleware(RateLimitMiddleware, limiter=limiter, prefix=API_PREFIX)]
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="RepoAtlas API",
        version=__version__,
        description=DESCRIPTION,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
        middleware=[
            Middleware(RequestContextMiddleware),
            Middleware(
                CORSMiddleware,
                allow_origins=settings.cors_origins,
                allow_methods=["GET", "POST"],
                allow_headers=["Content-Type", REQUEST_ID_HEADER],
                expose_headers=["Retry-After", REQUEST_ID_HEADER],
                max_age=600,
            ),
            Middleware(UnhandledErrorMiddleware),
            *limits,
            Middleware(EncodedKeyMiddleware, prefix=API_PREFIX),
        ],
    )
    app.state.rate_limiter = limiter
    app.state.settings = settings
    app.state.engine = engine
    app.state.store = GraphStore.from_settings(settings, engine)
    app.state.ready_snapshots = set()  # IDs of snapshots seen ready (they never change)
    errors.install(app)
    for router in (repos.router, jobs.router, snapshots.router, health.router):
        app.include_router(router, prefix=API_PREFIX)
    return app
