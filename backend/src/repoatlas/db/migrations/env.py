"""Alembic environment: uses the URL from the Alembic config, else RepoAtlas settings."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine, pool

import repoatlas.jobs.models
import repoatlas.retrieval.models
import repoatlas.store.models
import repoatlas.tracing.models  # noqa: F401  (registers the tables on Base.metadata)
from repoatlas.config import get_settings
from repoatlas.db.base import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _run_with(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Callers (such as tests) may pass an open connection through config.attributes.
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with(connection)
        return
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as new_connection:
            _run_with(new_connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
