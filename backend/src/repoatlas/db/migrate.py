"""Programmatic Alembic helpers.

Migrations ship inside the package (``repoatlas.db:migrations``), so the same code runs from a
source checkout, an installed wheel, or a container image — no ``alembic.ini`` needed.
"""

from __future__ import annotations

import re

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from repoatlas.db.engine import create_db_engine

SCRIPT_LOCATION = "repoatlas.db:migrations"
# alembic_version.version_num is VARCHAR(32).
MAX_REVISION_ID_LENGTH = 32


def alembic_config(database_url: str | None = None) -> Config:
    """Build an Alembic config in memory, optionally pointing at ``database_url``."""
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    # New migration files are named after their revision ID, e.g. 0002_add_repos.py.
    config.set_main_option("file_template", "%%(rev)s")
    if database_url is not None:
        # ConfigParser interpolation: a literal % (URL-encoded passwords) must be doubled.
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    """Apply migrations up to ``revision``."""
    command.upgrade(alembic_config(database_url), revision)


def current_revision(database_url: str) -> str | None:
    """Return the database's current revision, or ``None`` if no migration has run."""
    engine = create_db_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str | None:
    """Return the newest revision shipped with the package."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def next_revision_id(head: str | None, message: str) -> str:
    """Return a sequential, readable revision ID such as ``0002_add_repos_table``."""
    number = int(head.split("_", 1)[0]) + 1 if head else 1
    slug = re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_") or "revision"
    return f"{number:04d}_{slug}"[:MAX_REVISION_ID_LENGTH].rstrip("_")


def create_revision(database_url: str, message: str, *, autogenerate: bool = False) -> str:
    """Write a new migration file into the package and return its revision ID."""
    rev_id = next_revision_id(head_revision(), message)
    command.revision(
        alembic_config(database_url), message=message, autogenerate=autogenerate, rev_id=rev_id
    )
    return rev_id
