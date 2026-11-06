from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import structlog

from repoatlas.config import LogFormat, Settings
from repoatlas.logging import configure, configure_logging, get_logger


@pytest.fixture(autouse=True)
def reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def _json_lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.strip().splitlines()]


def test_json_logs_are_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", LogFormat.JSON)
    get_logger("tests").info("snapshot_built", files=42)

    (record,) = _json_lines(capsys.readouterr().err)
    assert record["event"] == "snapshot_built"
    assert record["files"] == 42
    assert record["level"] == "info"
    assert record["logger"] == "tests"
    assert "timestamp" in record


def test_messages_below_the_level_are_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", LogFormat.JSON)
    log = get_logger("tests")
    log.info("hidden")
    log.warning("shown")

    assert [record["event"] for record in _json_lines(capsys.readouterr().err)] == ["shown"]


def test_console_format_from_settings(capsys: pytest.CaptureFixture[str]) -> None:
    configure(Settings(_env_file=None, log_format=LogFormat.CONSOLE))
    get_logger().info("hello_console")
    assert "hello_console" in capsys.readouterr().err
