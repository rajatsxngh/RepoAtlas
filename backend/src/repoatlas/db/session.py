"""Session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from repoatlas.db.engine import get_engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on error."""
    session = make_session_factory(engine or get_engine())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
