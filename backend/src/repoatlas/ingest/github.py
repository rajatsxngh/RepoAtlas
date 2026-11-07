"""GitHub repository URLs and metadata.

Only ``https://github.com/<owner>/<repo>`` URLs are accepted (PRD ING-1). Metadata comes from the
GitHub REST API; transient gateway errors are retried, and a token raises the rate limit.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from urllib.parse import quote, urlsplit

import httpx

from repoatlas.config import Settings
from repoatlas.ingest.errors import (
    GitHubError,
    InvalidRepoUrl,
    RateLimited,
    RepoEmpty,
    RepoNotFound,
    RepoPrivate,
    RepoTooLarge,
)

GITHUB_HOST = "github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "RepoAtlas"
RETRY_STATUSES = frozenset({502, 503, 504})

# Owners: letters, digits, single inner hyphens, at most 39 characters.
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_TOKEN_HINT = "Set REPOATLAS_GITHUB_TOKEN to raise the GitHub API limit."


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        return f"https://{GITHUB_HOST}/{self.owner}/{self.name}.git"

    @property
    def key(self) -> str:
        """Case-insensitive identity: GitHub treats owner and repo names case-insensitively."""
        return self.full_name.lower()


@dataclass(frozen=True, slots=True)
class RepoMeta:
    ref: RepoRef
    default_branch: str
    private: bool
    size_kb: int
    html_url: str
    archived: bool
    fork: bool


def parse_repo_url(url: str) -> RepoRef:
    """Validate a GitHub repository URL and return its owner and name."""
    text = url.strip()
    if not text:
        raise InvalidRepoUrl("The URL is empty.")
    parts = urlsplit(text)
    if parts.scheme.lower() != "https":
        raise InvalidRepoUrl("Use an https:// URL, for example https://github.com/pallets/flask.")
    if "@" in parts.netloc:
        raise InvalidRepoUrl("URLs that contain credentials are not accepted.")
    try:
        port = parts.port
    except ValueError:
        raise InvalidRepoUrl("The URL has an invalid port.") from None
    if port is not None or parts.netloc.lower() != GITHUB_HOST:
        raise InvalidRepoUrl("Only https://github.com/<owner>/<repo> URLs are supported.")
    if parts.query or parts.fragment:
        raise InvalidRepoUrl("Remove the query string or fragment from the URL.")

    segments = parts.path.strip("/").split("/")
    if len(segments) != 2:
        raise InvalidRepoUrl("Expected https://github.com/<owner>/<repo>.")
    owner, name = segments
    name = name.removesuffix(".git")
    if not _OWNER_RE.fullmatch(owner):
        raise InvalidRepoUrl(f"'{_shorten(owner)}' is not a valid GitHub owner name.")
    if name in {".", ".."} or not _REPO_RE.fullmatch(name):
        raise InvalidRepoUrl(f"'{_shorten(name)}' is not a valid repository name.")
    return RepoRef(owner=owner, name=name)


def check_repo_size(meta: RepoMeta, max_size_kb: int) -> None:
    """Reject repositories whose GitHub-reported size exceeds the configured cap (ING-2)."""
    if meta.size_kb > max_size_kb:
        raise RepoTooLarge(
            f"{meta.ref.full_name} is about {meta.size_kb / 1024:.0f} MB on GitHub; "
            f"the limit is {max_size_kb / 1024:.0f} MB."
        )


class GitHubClient:
    """Minimal GitHub REST client for repository metadata and branch heads."""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        timeout: float = 15.0,
        retries: int = 2,
        backoff_s: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> GitHubClient:
        token = settings.github_token.get_secret_value() if settings.github_token else None
        return cls(token=token, base_url=settings.github_api_url)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_repo(self, ref: RepoRef) -> RepoMeta:
        """Return repository metadata; private repositories are rejected (ING-1)."""
        data = self._get(f"/repos/{ref.owner}/{ref.name}", ref).json()
        meta = RepoMeta(
            ref=RepoRef(owner=data["owner"]["login"], name=data["name"]),
            default_branch=data["default_branch"],
            private=bool(data["private"]),
            size_kb=int(data["size"]),
            html_url=data["html_url"],
            archived=bool(data.get("archived", False)),
            fork=bool(data.get("fork", False)),
        )
        if meta.private:
            raise RepoPrivate(f"{meta.ref.full_name} is private; only public repositories work.")
        return meta

    def resolve_head(self, ref: RepoRef, branch: str) -> str:
        """Return the commit SHA that ``branch`` currently points to."""
        path = f"/repos/{ref.owner}/{ref.name}/commits/{quote(branch, safe='/')}"
        sha = self._get(path, ref, accept="application/vnd.github.sha").text.strip()
        if not _SHA_RE.fullmatch(sha):
            raise GitHubError("GitHub returned an unexpected commit id.")
        return sha

    def _get(self, path: str, ref: RepoRef, *, accept: str | None = None) -> httpx.Response:
        headers = {"Accept": accept} if accept else None
        for attempt in range(self._retries + 1):
            last_attempt = attempt == self._retries
            try:
                response = self._http.get(path, headers=headers)
            except httpx.TransportError as exc:
                if last_attempt:
                    raise GitHubError(f"Could not reach GitHub ({type(exc).__name__}).") from exc
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in RETRY_STATUSES or last_attempt:
                    _raise_for_status(response, ref)
            self._sleep(self._backoff_s * (3**attempt))
        raise AssertionError("unreachable")  # pragma: no cover


def _raise_for_status(response: httpx.Response, ref: RepoRef) -> None:
    status = response.status_code
    if status == 404:
        raise RepoNotFound(
            f"{ref.full_name} was not found on GitHub. It may not exist, or it may be private."
        )
    if status == 409:
        raise RepoEmpty(f"{ref.full_name} has no commits yet.")
    if status == 429 or (status == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
        reset_at = _reset_time(response)
        when = f" until {reset_at:%H:%M} UTC" if reset_at else ""
        raise RateLimited(f"GitHub rate limit reached{when}. {_TOKEN_HINT}", reset_at=reset_at)
    if status == 401:
        raise GitHubError("GitHub rejected the token (HTTP 401). Check REPOATLAS_GITHUB_TOKEN.")
    if status in RETRY_STATUSES:
        raise GitHubError(f"GitHub is not responding (HTTP {status}). Try again. {_TOKEN_HINT}")
    raise GitHubError(f"GitHub returned HTTP {status} for {ref.full_name}.")


def _reset_time(response: httpx.Response) -> datetime | None:
    reset = response.headers.get("x-ratelimit-reset")
    if reset and reset.isdigit():
        return datetime.fromtimestamp(int(reset), tz=UTC)
    return None


def _shorten(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
