from __future__ import annotations

from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from repoatlas import __version__
from repoatlas.cli import app
from repoatlas.config import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"repoatlas {__version__}"


def test_help_lists_the_db_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "db" in result.output


def test_db_help_lists_commands() -> None:
    result = runner.invoke(app, ["db", "--help"])
    assert result.exit_code == 0
    for command in ("upgrade", "current", "revision"):
        assert command in result.output


def test_unreachable_database_gives_a_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REPOATLAS_DATABASE_URL",
        "postgresql+psycopg://repoatlas:s3cret-pass@127.0.0.1:1/repoatlas?connect_timeout=2",
    )
    result = runner.invoke(app, ["db", "current"])
    assert result.exit_code == 1
    assert "Could not connect to Postgres" in result.output
    assert "docker compose" in result.output
    assert "s3cret-pass" not in result.output
