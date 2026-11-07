from __future__ import annotations

import io
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoatlas import __version__, cli
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


def test_json_output_is_utf8_whatever_the_console_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    cli._echo_json('{"name": "caf\u00e9 \u540d\u524d \U0001f600"}\n')
    assert raw.getvalue().decode("utf-8") == '{"name": "caf\u00e9 \u540d\u524d \U0001f600"}\n'


def test_json_output_falls_back_to_text_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    text = io.StringIO()
    monkeypatch.setattr(sys, "stdout", text)
    cli._echo_json("{}\n")
    assert text.getvalue() == "{}\n"


def test_graph_json_to_stdout_keeps_unicode(tmp_path: Path) -> None:
    (tmp_path / "caf\u00e9.py").write_text("def gr\u00f6\u00dfe():\n    pass\n", encoding="utf-8")
    result = runner.invoke(app, ["graph", str(tmp_path), "--json", "-"])
    assert result.exit_code == 0, result.output
    assert "py:caf\u00e9.py:gr\u00f6\u00dfe:function" in result.stdout
