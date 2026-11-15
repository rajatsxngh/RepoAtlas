"""Freshness: the remote head by ``git ls-remote``, the comparison, and its five-minute cache."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from repoatlas.ingest.errors import FetchError, GitHubError, RepoNotFound
from repoatlas.ingest.freshness import CACHE_TTL_S, FreshnessChecker, remote_head
from repoatlas.ingest.github import GitHubClient, RepoRef
from tests.unit.ingest.gitrepo import OriginRepo, Spec

REF = RepoRef(owner="acme", name="shop")
OLD = "a" * 40
NEW = "b" * 40


def test_remote_head_reads_the_branch_with_ls_remote(tmp_path: Path) -> None:
    origin = OriginRepo(tmp_path / "origin.git")
    first = origin.commit([Spec("a.py", b"x = 1\n")])
    assert remote_head(origin.url, "main") == first
    second = origin.commit([Spec("a.py", b"x = 2\n")])
    assert second != first
    assert remote_head(origin.url, "main") == second


def test_remote_head_of_a_missing_branch_or_repository_fails(tmp_path: Path) -> None:
    origin = OriginRepo(tmp_path / "origin.git")
    origin.commit([Spec("a.py", b"x = 1\n")])
    with pytest.raises(FetchError, match="no branch named gone"):
        remote_head(origin.url, "gone")
    with pytest.raises(FetchError):
        remote_head((tmp_path / "nowhere.git").resolve().as_uri(), "main")


@pytest.mark.parametrize("branch", ["", "-upload-pack=x", "a..b", "feature *", "x\ny", "*"])
def test_remote_head_refuses_branch_names_that_are_not_plain(tmp_path: Path, branch: str) -> None:
    with pytest.raises(FetchError, match="can't be looked up"):
        remote_head("https://github.com/acme/shop.git", branch)


class Remote:
    """A repository whose head the test moves, counting how often it is asked."""

    def __init__(self, head: str, behind: int | None = 3) -> None:
        self.head = head
        self.behind: int | Exception | None = behind
        self.head_calls = 0
        self.compare_calls: list[tuple[str, str]] = []
        self.now = 1_000.0

    def head_of(self, _ref: RepoRef, _branch: str) -> str:
        self.head_calls += 1
        return self.head

    def count(self, _ref: RepoRef, base: str, head: str) -> int | None:
        self.compare_calls.append((base, head))
        if isinstance(self.behind, Exception):
            raise self.behind
        return self.behind

    def checker(self) -> FreshnessChecker:
        return FreshnessChecker(self.head_of, self.count, clock=lambda: self.now)


def test_a_snapshot_at_the_head_is_fresh_and_nothing_is_compared() -> None:
    remote = Remote(OLD)
    result = remote.checker().check(REF, "main", OLD)
    assert (result.stale, result.commits_behind) == (False, 0)
    assert (result.snapshot_commit, result.remote_head, result.default_branch) == (OLD, OLD, "main")
    assert remote.compare_calls == []


def test_a_snapshot_behind_the_head_is_stale_with_a_count() -> None:
    remote = Remote(NEW, behind=7)
    result = remote.checker().check(REF, "main", OLD)
    assert (result.stale, result.remote_head, result.commits_behind) == (True, NEW, 7)
    assert remote.compare_calls == [(OLD, NEW)]


def test_answers_are_reused_for_five_minutes_per_repository() -> None:
    remote = Remote(OLD)
    checker = remote.checker()
    checker.check(REF, "main", OLD)
    remote.head = NEW  # the branch moves, but the answer is still warm
    remote.now += CACHE_TTL_S - 1
    assert checker.check(RepoRef(owner="ACME", name="Shop"), "main", OLD).stale is False
    assert remote.head_calls == 1
    remote.now += 2
    assert checker.check(REF, "main", OLD).stale is True
    assert remote.head_calls == 2
    # Another repository and another branch have their own answers.
    checker.check(RepoRef(owner="acme", name="other"), "main", OLD)
    checker.check(REF, "dev", OLD)
    assert remote.head_calls == 4


def test_the_count_is_cached_with_the_head() -> None:
    remote = Remote(NEW)
    checker = remote.checker()
    for _ in range(3):
        assert checker.check(REF, "main", OLD).commits_behind == 3
    assert len(remote.compare_calls) == 1
    # Another snapshot of the same repository is compared on its own.
    assert checker.check(REF, "main", "c" * 40).commits_behind == 3
    assert len(remote.compare_calls) == 2


@pytest.mark.parametrize("behind", [None, 0, GitHubError("GitHub is down")])
def test_a_count_github_cannot_give_is_none(behind: int | Exception | None) -> None:
    remote = Remote(NEW)
    remote.behind = behind
    result = remote.checker().check(REF, "main", OLD)
    assert (result.stale, result.commits_behind) == (True, None)


def test_a_failed_lookup_is_raised_and_not_cached() -> None:
    remote = Remote(OLD)
    calls = 0

    def failing(_ref: RepoRef, _branch: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FetchError("git ls-remote failed: could not resolve host")
        return OLD

    checker = FreshnessChecker(failing, remote.count, clock=lambda: remote.now)
    with pytest.raises(FetchError):
        checker.check(REF, "main", OLD)
    assert checker.check(REF, "main", OLD).stale is False
    assert calls == 2


def client(handler: httpx.MockTransport) -> GitHubClient:
    return GitHubClient(transport=handler, retries=0)


def test_commits_ahead_reads_the_compare_endpoint() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ahead", "ahead_by": 12, "behind_by": 0})

    with client(httpx.MockTransport(answer)) as github:
        assert github.commits_ahead(REF, OLD, NEW) == 12
    assert seen == [f"/repos/acme/shop/compare/{OLD}...{NEW}"]


def test_commits_ahead_without_a_number_is_none() -> None:
    with client(httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as github:
        assert github.commits_ahead(REF, OLD, NEW) is None
    with client(httpx.MockTransport(lambda _r: httpx.Response(200, json={"ahead_by": True}))) as g:
        assert g.commits_ahead(REF, OLD, NEW) is None


def test_commits_ahead_needs_full_shas_and_reports_github_errors() -> None:
    with client(httpx.MockTransport(lambda _r: httpx.Response(404, json={}))) as github:
        with pytest.raises(GitHubError, match="two full commit SHAs"):
            github.commits_ahead(REF, "main", NEW)
        with pytest.raises(RepoNotFound):
            github.commits_ahead(REF, OLD, NEW)
