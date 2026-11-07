"""Settings loaded from ``REPOATLAS_*`` environment variables and the nearest ``.env`` file."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "REPOATLAS_"
DEFAULT_DATABASE_URL = "postgresql+psycopg://repoatlas:repoatlas@localhost:5433/repoatlas"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


def find_env_file(start: Path | None = None) -> Path | None:
    """Return the nearest ``.env`` file at or above ``start`` (default: the working directory)."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


class Settings(BaseSettings):
    """Process-wide configuration.

    Environment variables win over the ``.env`` file. Secrets are ``SecretStr`` so they never
    appear in reprs, logs, or tracebacks.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=find_env_file(),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    database_url: str = DEFAULT_DATABASE_URL
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
    github_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    data_dir: Path = Path(".repoatlas")

    # Ingestion
    github_api_url: str = "https://api.github.com"
    max_repo_size_kb: int = 500_000  # GitHub-reported size, which includes history
    git_timeout_s: float = 300.0
    history_depth: int = 1000  # generations of history fetched for ownership analysis

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        """Accept plain ``postgres://`` URLs (common on hosting platforms); always use psycopg 3."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process settings (tests call ``get_settings.cache_clear()``)."""
    return Settings()
