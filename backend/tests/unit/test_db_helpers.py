from __future__ import annotations

import pytest

from repoatlas.db import engine as engine_module
from repoatlas.db import migrate
from repoatlas.db.engine import redact_url


def test_head_revision_is_the_job_queue() -> None:
    assert migrate.head_revision() == "0007_impact_runs"


def test_alembic_config_keeps_percent_encoded_passwords() -> None:
    url = "postgresql+psycopg://user:p%40ss@db:5432/repoatlas"
    assert migrate.alembic_config(url).get_main_option("sqlalchemy.url") == url


@pytest.mark.parametrize(
    ("head", "message", "expected"),
    [
        (None, "Baseline", "0001_baseline"),
        ("0001_baseline", "Add repos table", "0002_add_repos_table"),
        ("0009_x", "!!!", "0010_revision"),
    ],
)
def test_next_revision_id(head: str | None, message: str, expected: str) -> None:
    assert migrate.next_revision_id(head, message) == expected


def test_next_revision_id_fits_the_version_column() -> None:
    revision_id = migrate.next_revision_id("0001_baseline", "a very long description " * 4)
    assert len(revision_id) <= migrate.MAX_REVISION_ID_LENGTH
    assert revision_id.startswith("0002_")
    assert not revision_id.endswith("_")


def test_redact_url_hides_the_password() -> None:
    redacted = redact_url("postgresql+psycopg://user:secret@db:5433/repoatlas")
    assert "secret" not in redacted
    assert "user:***@db:5433/repoatlas" in redacted


def test_redact_url_handles_garbage() -> None:
    assert redact_url("not a url") == "<invalid database URL>"


def test_create_db_engine_can_bound_connection_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_create_engine(url: str, **kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(engine_module, "create_engine", fake_create_engine)
    engine_module.create_db_engine("postgresql+psycopg://u@h/d")
    engine_module.create_db_engine("postgresql+psycopg://u@h/d", connect_timeout_s=2.2)
    engine_module.create_db_engine("postgresql+psycopg://u@h/d", connect_timeout_s=0.1)
    assert [call["connect_args"] for call in calls] == [
        {},
        {"connect_timeout": 3},
        {"connect_timeout": 1},
    ]
    assert all(call["pool_pre_ping"] is True for call in calls)
