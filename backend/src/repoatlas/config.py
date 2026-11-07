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
    owner_commits: int = 1000  # most recent commits read for file owners
    max_source_files: int = 5_000  # after skip rules
    max_file_bytes: int = 1_000_000

    # Graph store
    graph_cache_mb: int = 512  # estimated memory for cached snapshot adjacency
    max_read_lines: int = 400  # lines one code read returns at most

    # API
    web_origins: str = "http://localhost:3000"  # comma-separated origins allowed by CORS
    db_connect_timeout_s: float = 10.0  # opening one database connection, in the API and worker
    rate_limit_enabled: bool = True
    builds_per_hour: int = 5  # build requests per client
    reads_per_minute: int = 120  # other API requests per client
    rate_limit_salt: SecretStr | None = None  # hashes client addresses; random per process if unset

    # Background jobs and the worker
    job_lease_s: float = 60.0  # a running job whose worker sends no heartbeat this long is requeued
    job_heartbeat_s: float = 15.0
    job_max_attempts: int = 3
    job_retry_backoff_s: tuple[float, ...] = (10.0, 60.0, 300.0)  # wait after attempt 1, 2, 3+
    job_timeout_s: float = 3600.0  # one attempt may run at most this long
    worker_poll_s: float = 2.0  # how often an idle worker looks for due jobs

    @property
    def cors_origins(self) -> list[str]:
        """The web app's origins, from the comma-separated ``web_origins``."""
        return [
            origin.strip().rstrip("/") for origin in self.web_origins.split(",") if origin.strip()
        ]

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
