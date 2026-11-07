from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from repoatlas.ingest.errors import (
    GitHubError,
    RateLimited,
    RepoEmpty,
    RepoNotFound,
    RepoPrivate,
    RepoTooLarge,
)
from repoatlas.ingest.github import GitHubClient, RepoMeta, RepoRef, check_repo_size

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "github"
FLASK = RepoRef(owner="pallets", name="flask")
SHA = "49943b5a94413a530761350f04e896f76e8d620e"

Handler = Callable[[httpx.Request], httpx.Response]


def fixture_json(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def client(handler: Handler, *, token: str | None = None) -> GitHubClient:
    return GitHubClient(
        token=token,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def test_get_repo_parses_recorded_metadata() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("repo_pallets_flask.json"))

    with client(handler) as github:
        meta = github.get_repo(FLASK)

    assert meta == RepoMeta(
        ref=FLASK,
        default_branch="main",
        private=False,
        size_kb=12227,
        html_url="https://github.com/pallets/flask",
        archived=False,
        fork=False,
    )
    (request,) = seen
    assert request.url.path == "/repos/pallets/flask"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["User-Agent"] == "RepoAtlas"
    assert "Authorization" not in request.headers


def test_token_is_sent_as_bearer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture_json("repo_pallets_flask.json"))

    with client(handler, token="ghp_example") as github:
        github.get_repo(FLASK)
    assert seen[0].headers["Authorization"] == "Bearer ghp_example"


def test_not_found_mentions_private_repos() -> None:
    with (
        client(lambda _r: httpx.Response(404, json=fixture_json("not_found.json"))) as github,
        pytest.raises(RepoNotFound, match="may be private"),
    ):
        github.get_repo(FLASK)


def test_private_repositories_are_rejected() -> None:
    data = {**fixture_json("repo_pallets_flask.json"), "private": True}
    with (
        client(lambda _r: httpx.Response(200, json=data)) as github,
        pytest.raises(RepoPrivate),
    ):
        github.get_repo(FLASK)


def test_rate_limit_reports_reset_time_and_token_hint() -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"}
    with (
        client(lambda _r: httpx.Response(403, headers=headers, json={})) as github,
        pytest.raises(RateLimited, match="REPOATLAS_GITHUB_TOKEN") as excinfo,
    ):
        github.get_repo(FLASK)
    assert excinfo.value.reset_at is not None
    assert int(excinfo.value.reset_at.timestamp()) == 1800000000


def test_http_429_is_rate_limited() -> None:
    with (
        client(lambda _r: httpx.Response(429, json={})) as github,
        pytest.raises(RateLimited),
    ):
        github.get_repo(FLASK)


def test_gateway_errors_are_retried_then_succeed() -> None:
    responses = iter(
        [
            httpx.Response(504),
            httpx.Response(502),
            httpx.Response(200, json=fixture_json("repo_pallets_flask.json")),
        ]
    )
    with client(lambda _r: next(responses)) as github:
        assert github.get_repo(FLASK).default_branch == "main"


def test_persistent_gateway_errors_give_up_with_a_clear_message() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(504)

    with client(handler) as github, pytest.raises(GitHubError, match="not responding"):
        github.get_repo(FLASK)
    assert calls == 3


def test_network_errors_become_github_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with client(handler) as github, pytest.raises(GitHubError, match="Could not reach GitHub"):
        github.get_repo(FLASK)


def test_resolve_head_returns_the_sha() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=SHA + "\n")

    with client(handler) as github:
        assert github.resolve_head(FLASK, "main") == SHA
    assert seen[0].url.path == "/repos/pallets/flask/commits/main"
    assert seen[0].headers["Accept"] == "application/vnd.github.sha"


def test_resolve_head_rejects_unexpected_bodies() -> None:
    with (
        client(lambda _r: httpx.Response(200, text="<html>oops</html>")) as github,
        pytest.raises(GitHubError, match="unexpected commit id"),
    ):
        github.resolve_head(FLASK, "main")


def test_empty_repositories_are_reported() -> None:
    with (
        client(lambda _r: httpx.Response(409, json={"message": "Git Repository is empty."})) as gh,
        pytest.raises(RepoEmpty),
    ):
        gh.resolve_head(FLASK, "main")


def test_repo_size_cap() -> None:
    meta = RepoMeta(FLASK, "main", False, size_kb=600_000, html_url="", archived=False, fork=False)
    check_repo_size(meta, max_size_kb=700_000)
    with pytest.raises(RepoTooLarge, match="limit is 488 MB"):
        check_repo_size(meta, max_size_kb=500_000)
