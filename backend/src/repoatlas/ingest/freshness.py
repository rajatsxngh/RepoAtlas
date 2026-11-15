"""Is a snapshot still the head of its repository's default branch? (PRF-4)

``remote_head`` asks the repository itself with ``git ls-remote`` (one small request, no clone,
no GitHub API quota). ``FreshnessChecker`` compares a snapshot's commit with it and, when they
differ, asks GitHub how many commits the branch is ahead; that number is a courtesy for the
banner and is ``None`` whenever it can't be told. Answers are kept per repository for five
minutes, so a map that is open in many tabs asks GitHub once.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from repoatlas.config import Settings
from repoatlas.ingest.errors import FetchError, GitHubError, IngestError
from repoatlas.ingest.git import run_git
from repoatlas.ingest.github import GitHubClient, RepoRef

CACHE_TTL_S = 300.0
LS_REMOTE_TIMEOUT_S = 20.0
_SHA = re.compile(r"[0-9a-f]{40}")
# Branch names as git allows them, minus anything that could be read as an option or a pattern.
_BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/+@-]{0,254}")


def remote_head(clone_url: str, branch: str, *, timeout: float = LS_REMOTE_TIMEOUT_S) -> str:
    """The commit ``branch`` points to at ``clone_url`` (``git ls-remote``)."""
    if not _BRANCH.fullmatch(branch) or ".." in branch:
        raise FetchError("The repository's default branch has a name that can't be looked up.")
    ref = f"refs/heads/{branch}"
    listing = run_git(["ls-remote", clone_url, ref], timeout=timeout).decode("utf-8", "replace")
    for line in listing.splitlines():
        sha, _tab, name = line.partition("\t")
        if name.strip() == ref and _SHA.fullmatch(sha.strip()):
            return sha.strip()
    raise FetchError(f"The repository has no branch named {branch}.")


@dataclass(frozen=True, slots=True)
class RemoteState:
    """What a repository's default branch looked like at ``checked_at``."""

    head: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    snapshot_commit: str
    remote_head: str
    stale: bool
    commits_behind: int | None
    default_branch: str
    checked_at: datetime


class FreshnessChecker:
    """Compares snapshot commits with their repositories' default branches, with a cache.

    ``head_of(ref, branch)`` and ``behind(ref, base, head)`` are the two lookups (``git
    ls-remote`` and GitHub's compare endpoint by default); tests pass their own.
    """

    def __init__(
        self,
        head_of: Callable[[RepoRef, str], str],
        behind: Callable[[RepoRef, str, str], int | None],
        *,
        ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._head_of = head_of
        self._behind = behind
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._heads: dict[tuple[str, str], tuple[float, RemoteState]] = {}
        self._counts: dict[tuple[str, str, str], tuple[float, int | None]] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> FreshnessChecker:
        def head_of(ref: RepoRef, branch: str) -> str:
            return remote_head(ref.clone_url, branch)

        def behind(ref: RepoRef, base: str, head: str) -> int | None:
            try:
                with GitHubClient.from_settings(settings) as github:
                    return github.commits_ahead(ref, base, head)
            except IngestError:
                return None  # the banner then says "behind" without a number

        return cls(head_of, behind)

    def check(self, ref: RepoRef, branch: str, snapshot_commit: str) -> FreshnessResult:
        """Whether ``snapshot_commit`` is still the head of ``branch``. Raises ``FetchError``
        when the repository can't be asked (the caller answers 502)."""
        state = self._head(ref, branch)
        stale = state.head != snapshot_commit
        return FreshnessResult(
            snapshot_commit=snapshot_commit,
            remote_head=state.head,
            stale=stale,
            commits_behind=self._count(ref, snapshot_commit, state.head) if stale else 0,
            default_branch=branch,
            checked_at=state.checked_at,
        )

    def _head(self, ref: RepoRef, branch: str) -> RemoteState:
        key = (ref.key, branch)
        now = self._clock()
        with self._lock:
            cached = self._heads.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
        state = RemoteState(head=self._head_of(ref, branch), checked_at=datetime.now(UTC))
        with self._lock:
            self._heads[key] = (now + self._ttl_s, state)
            self._prune(now)
        return state

    def _count(self, ref: RepoRef, base: str, head: str) -> int | None:
        key = (ref.key, base, head)
        now = self._clock()
        with self._lock:
            cached = self._counts.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
        try:
            count = self._behind(ref, base, head)
        except GitHubError:
            count = None
        if count is not None and count < 1:
            count = None  # the branch moved, yet GitHub counts nothing ahead (a rewrite)
        with self._lock:
            self._counts[key] = (now + self._ttl_s, count)
        return count

    def _prune(self, now: float) -> None:
        self._heads = {key: kept for key, kept in self._heads.items() if kept[0] > now}
        self._counts = {key: kept for key, kept in self._counts.items() if kept[0] > now}
