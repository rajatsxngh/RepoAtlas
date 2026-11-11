from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.config import get_settings
from repoatlas.db import migrate


def test_baseline_enables_the_extensions(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        installed = set(connection.execute(text("SELECT extname FROM pg_extension")).scalars())
    assert {"vector", "pg_trgm"} <= installed


def test_database_is_at_the_latest_revision(pg_url: str) -> None:
    assert migrate.current_revision(pg_url) == migrate.head_revision() == "0005_tracing"


def test_upgrade_is_idempotent(pg_url: str) -> None:
    migrate.upgrade(pg_url)
    assert migrate.current_revision(pg_url) == "0005_tracing"


def test_vector_nearest_neighbor_query(db_session: Session) -> None:
    db_session.execute(text("CREATE TABLE probe_vectors (v vector(3))"))
    db_session.execute(text("INSERT INTO probe_vectors VALUES ('[1,2,3]'), ('[3,2,1]')"))
    nearest = db_session.execute(
        text("SELECT v::text FROM probe_vectors ORDER BY v <-> '[1,2,2]' LIMIT 1")
    ).scalar_one()
    assert nearest == "[1,2,3]"


def test_trigram_similarity(db_session: Session) -> None:
    score = db_session.execute(text("SELECT similarity('graph_store', 'graphstore')")).scalar_one()
    assert score > 0.3


def test_db_session_changes_stay_invisible_and_are_rolled_back(
    db_session: Session, pg_engine: Engine
) -> None:
    db_session.execute(text("CREATE TABLE probe_rollback (id int)"))
    db_session.commit()  # only releases a savepoint; the outer transaction is still open
    with pg_engine.connect() as other:
        assert other.execute(text("SELECT to_regclass('probe_rollback')")).scalar() is None


@pytest.mark.parametrize("command", [["db", "current"], ["db", "upgrade"]])
def test_cli_reports_the_revision(
    pg_url: str, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", pg_url)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, command)
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "0005_tracing" in result.output
