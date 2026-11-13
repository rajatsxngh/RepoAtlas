"""The error model and how ingestion and store errors map onto it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from repoatlas.api.errors import (
    ApiError,
    ErrorCode,
    documented,
    error_response,
    from_ingest_error,
    from_store_error,
)
from repoatlas.ingest.errors import (
    FetchError,
    GitHubError,
    IngestError,
    InvalidRepoUrl,
    RateLimited,
    RepoEmpty,
    RepoNotFound,
    RepoPrivate,
    RepoTooLarge,
)
from repoatlas.store.errors import (
    InvalidQuery,
    NodeNotFound,
    PathNotInSnapshot,
    SnapshotNotFound,
    SnapshotNotReady,
    StoreError,
    TextNotStored,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def test_error_codes_are_the_documented_stable_set() -> None:
    assert {code.value for code in ErrorCode} == {
        "invalid_request",
        "unauthorized",
        "invalid_url",
        "repo_private",
        "repo_too_large",
        "repo_empty",
        "not_found",
        "rate_limited",
        "snapshot_not_ready",
        "upstream_unavailable",
        "llm_unavailable",
        "answer_failed",
        "internal",
    }


def test_error_response_has_the_one_error_model() -> None:
    response = error_response(
        409, ErrorCode.SNAPSHOT_NOT_READY, "Not yet.", details={"job_id": 3}, headers={"X": "1"}
    )
    assert response.status_code == 409
    assert response.headers["X"] == "1"
    assert response.body == (
        b'{"error":{"code":"snapshot_not_ready","message":"Not yet.","details":{"job_id":3}}}'
    )
    plain = ApiError(422, ErrorCode.INVALID_REQUEST, "Bad.").response()
    assert plain.body == b'{"error":{"code":"invalid_request","message":"Bad.","details":{}}}'


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidRepoUrl("Only https://github.com/<owner>/<repo> URLs."), 422, "invalid_url"),
        (RepoPrivate("acme/secret is private."), 403, "repo_private"),
        (RepoTooLarge("acme/huge is too large."), 413, "repo_too_large"),
        (RepoEmpty("acme/empty has no commits yet."), 422, "repo_empty"),
        (RepoNotFound("acme/gone was not found on GitHub."), 404, "not_found"),
        (GitHubError("GitHub returned HTTP 500."), 502, "upstream_unavailable"),
        (FetchError("git fetch failed."), 502, "upstream_unavailable"),
        (IngestError("Something else went wrong."), 502, "upstream_unavailable"),
    ],
)
def test_ingest_errors_map_to_status_and_code(error: IngestError, status: int, code: str) -> None:
    mapped = from_ingest_error(error)
    assert (mapped.status_code, mapped.code.value, mapped.message) == (status, code, str(error))


def test_a_missing_repo_names_the_resource() -> None:
    assert from_ingest_error(RepoNotFound("gone")).details == {"resource": "repo"}


def test_github_rate_limits_tell_when_to_retry() -> None:
    reset = NOW + timedelta(seconds=90.2)
    mapped = from_ingest_error(RateLimited("GitHub rate limit reached.", reset_at=reset), now=NOW)
    assert (mapped.status_code, mapped.code) == (503, ErrorCode.RATE_LIMITED)
    assert mapped.headers == {"Retry-After": "91"}
    assert mapped.details == {"upstream": "github", "reset_at": reset.isoformat()}
    past = from_ingest_error(RateLimited("Reached.", reset_at=NOW - timedelta(seconds=5)), now=NOW)
    assert past.headers == {"Retry-After": "1"}
    unknown = from_ingest_error(RateLimited("Reached."), now=NOW)
    assert (unknown.headers, unknown.details) == ({}, {"upstream": "github"})


@pytest.mark.parametrize(
    ("error", "status", "code", "details"),
    [
        (
            SnapshotNotFound("Snapshot 9 does not exist."),
            404,
            "not_found",
            {"resource": "snapshot"},
        ),
        (NodeNotFound("Snapshot 1 has no node 'x'."), 404, "not_found", {"resource": "node"}),
        (PathNotInSnapshot("Snapshot 1 has no file 'a'."), 404, "not_found", {"resource": "file"}),
        (TextNotStored("a.png was skipped (binary)."), 404, "not_found", {"resource": "text"}),
        (InvalidQuery("The depth must be between 1 and 20."), 422, "invalid_request", {}),
        (
            SnapshotNotReady("Snapshot 1 is building.", status="building"),
            409,
            "snapshot_not_ready",
            {"status": "building"},
        ),
    ],
)
def test_store_errors_map_to_status_and_code(
    error: StoreError, status: int, code: str, details: dict[str, str]
) -> None:
    mapped = from_store_error(error)
    assert (mapped.status_code, mapped.code.value, mapped.message) == (status, code, str(error))
    assert mapped.details == details


def test_an_unknown_store_error_is_internal_and_says_nothing_more() -> None:
    mapped = from_store_error(StoreError("connection string with a password"))
    assert (mapped.status_code, mapped.code) == (500, ErrorCode.INTERNAL)
    assert "password" not in mapped.message


def test_documented_responses_are_sorted_and_use_the_error_model() -> None:
    responses = documented(404, 409)
    assert list(responses) == [404, 409, 422, 429, 500]
    assert responses[409]["description"] == "Conflict"
