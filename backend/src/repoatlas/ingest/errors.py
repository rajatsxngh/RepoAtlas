"""Ingestion errors. ``str(error)`` is always a message that is safe to show to users."""

from __future__ import annotations

from datetime import datetime


class IngestError(Exception):
    """Base class for every ingestion failure; ``code`` is a stable machine-readable name."""

    code = "ingest_error"


class InvalidRepoUrl(IngestError):
    code = "invalid_url"


class RepoNotFound(IngestError):
    code = "not_found"


class RepoPrivate(IngestError):
    code = "repo_private"


class RepoEmpty(IngestError):
    code = "repo_empty"


class RepoTooLarge(IngestError):
    code = "repo_too_large"


class RateLimited(IngestError):
    code = "rate_limited"

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GitHubError(IngestError):
    code = "github_error"


class FetchError(IngestError):
    code = "fetch_failed"
