from __future__ import annotations

import pytest

from repoatlas.db import migrate
from repoatlas.db.engine import redact_url


def test_head_revision_is_the_graph_store() -> None:
    assert migrate.head_revision() == "0002_graph_store"


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
