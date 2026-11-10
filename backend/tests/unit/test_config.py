from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from repoatlas.config import DEFAULT_DATABASE_URL, LogFormat, Settings, find_env_file, get_settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("REPOATLAS_"):
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load(**overrides: object) -> Settings:
    """Settings that ignore any developer .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults() -> None:
    settings = load()
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.log_level == "INFO"
    assert settings.log_format is LogFormat.CONSOLE
    assert settings.github_token is None
    assert settings.anthropic_api_key is None
    assert settings.data_dir == Path(".repoatlas")
    assert settings.embedding_model == "jinaai/jina-embeddings-v2-base-code"
    assert settings.embedding_threads is None
    assert (settings.graph_cache_mb, settings.max_read_lines) == (512, 400)


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOATLAS_LOG_LEVEL", "debug")
    monkeypatch.setenv("REPOATLAS_LOG_FORMAT", "json")
    settings = load()
    assert settings.log_level == "DEBUG"
    assert settings.log_format is LogFormat.JSON


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOATLAS_LOG_LEVEL", "loud")
    with pytest.raises(ValidationError):
        load()


@pytest.mark.parametrize("given", ["postgres://u:p@db:5432/d", "postgresql://u:p@db:5432/d"])
def test_plain_postgres_urls_use_psycopg(monkeypatch: pytest.MonkeyPatch, given: str) -> None:
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", given)
    assert load().database_url == "postgresql+psycopg://u:p@db:5432/d"


def test_secrets_never_appear_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOATLAS_GITHUB_TOKEN", "ghp_supersecret")
    settings = load()
    assert "ghp_supersecret" not in repr(settings)
    assert "ghp_supersecret" not in str(settings)
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_supersecret"


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOATLAS_GITHUB_TOKEN", "")
    assert load().github_token is None


def test_env_file_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("REPOATLAS_LOG_LEVEL=WARNING\n", encoding="utf-8")
    assert Settings(_env_file=env_file).log_level == "WARNING"


def test_environment_wins_over_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("REPOATLAS_LOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.setenv("REPOATLAS_LOG_LEVEL", "ERROR")
    assert Settings(_env_file=env_file).log_level == "ERROR"


def test_find_env_file_returns_the_nearest_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")
    project = tmp_path / "project"
    nested = project / "backend" / "src"
    nested.mkdir(parents=True)
    assert find_env_file(nested) == (tmp_path / ".env").resolve()

    (project / ".env").write_text("", encoding="utf-8")
    assert find_env_file(nested) == (project / ".env").resolve()


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
