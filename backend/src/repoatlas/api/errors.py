"""The API's one error model (API-1): ``{"error": {"code", "message", "details"}}``.

``code`` is a stable, machine-readable name (``ErrorCode``); ``message`` is safe to show to
people; ``details`` holds structured context, such as the resource that was not found, a
snapshot's status and build job, or the parameters that failed validation. Errors from
ingestion and the store map onto codes here, so routes can simply let them propagate.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, JsonValue
from starlette.exceptions import HTTPException as StarletteHTTPException

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


class ErrorCode(StrEnum):
    """Every ``error.code`` the API returns. Codes are stable: clients may branch on them."""

    INVALID_REQUEST = "invalid_request"  # a malformed request: parameters, body, method
    INVALID_URL = "invalid_url"  # not an https://github.com/<owner>/<repo> URL
    REPO_PRIVATE = "repo_private"
    REPO_TOO_LARGE = "repo_too_large"
    REPO_EMPTY = "repo_empty"  # the repository has no commits
    NOT_FOUND = "not_found"  # details.resource says what: snapshot, job, node, file, …
    RATE_LIMITED = "rate_limited"  # by this API (429) or by GitHub (503); see Retry-After
    SNAPSHOT_NOT_READY = "snapshot_not_ready"  # details: snapshot_id, status, job_id
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"  # GitHub could not be reached or failed
    LLM_UNAVAILABLE = "llm_unavailable"  # answers are not configured (no API key)
    ANSWER_FAILED = "answer_failed"  # the model could not be reached or gave unusable output
    INTERNAL = "internal"  # a bug or outage on our side; details.request_id identifies it


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str
    details: dict[str, JsonValue] = {}


class ErrorResponse(BaseModel):
    """The body of every error response."""

    model_config = ConfigDict(frozen=True)

    error: ErrorBody


class ApiError(Exception):
    """An error with its HTTP status, code, message, details, and extra headers."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, JsonValue] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details: dict[str, JsonValue] = dict(details or {})
        self.headers: dict[str, str] = dict(headers or {})

    def response(self) -> JSONResponse:
        return error_response(
            self.status_code, self.code, self.message, details=self.details, headers=self.headers
        )


def error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    *,
    details: Mapping[str, JsonValue] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=dict(details or {})))
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )


def not_found(resource: str, message: str, **details: JsonValue) -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, message, details={"resource": resource, **details})


# --- domain errors ------------------------------------------------------------------------

_INGEST_ERRORS: tuple[tuple[type[IngestError], int, ErrorCode], ...] = (
    (InvalidRepoUrl, 422, ErrorCode.INVALID_URL),
    (RepoPrivate, 403, ErrorCode.REPO_PRIVATE),
    (RepoTooLarge, 413, ErrorCode.REPO_TOO_LARGE),
    (RepoEmpty, 422, ErrorCode.REPO_EMPTY),
    (RepoNotFound, 404, ErrorCode.NOT_FOUND),
    (RateLimited, 503, ErrorCode.RATE_LIMITED),
    (GitHubError, 502, ErrorCode.UPSTREAM_UNAVAILABLE),
    (FetchError, 502, ErrorCode.UPSTREAM_UNAVAILABLE),
)

_NOT_FOUND_RESOURCES: tuple[tuple[type[StoreError], str], ...] = (
    (SnapshotNotFound, "snapshot"),
    (NodeNotFound, "node"),
    (PathNotInSnapshot, "file"),
    (TextNotStored, "text"),
)


def from_ingest_error(error: IngestError, *, now: datetime | None = None) -> ApiError:
    """The API error for an ingestion failure (``str(error)`` is always safe to show)."""
    status_code, code = next(
        ((status, code) for kind, status, code in _INGEST_ERRORS if isinstance(error, kind)),
        (502, ErrorCode.UPSTREAM_UNAVAILABLE),
    )
    details: dict[str, JsonValue] = {}
    headers: dict[str, str] = {}
    if isinstance(error, RepoNotFound):
        details["resource"] = "repo"
    if isinstance(error, RateLimited):
        details["upstream"] = "github"
        if error.reset_at is not None:
            details["reset_at"] = error.reset_at.isoformat()
            wait = (error.reset_at - (now or datetime.now(UTC))).total_seconds()
            headers["Retry-After"] = str(max(1, math.ceil(wait)))
    return ApiError(status_code, code, str(error), details=details, headers=headers)


def from_store_error(error: StoreError) -> ApiError:
    """The API error for a store failure; messages name only what the request named."""
    if isinstance(error, SnapshotNotReady):
        return ApiError(
            409,
            ErrorCode.SNAPSHOT_NOT_READY,
            str(error),
            details={"status": error.status},
        )
    if isinstance(error, InvalidQuery):
        return ApiError(422, ErrorCode.INVALID_REQUEST, str(error))
    for kind, resource in _NOT_FOUND_RESOURCES:
        if isinstance(error, kind):
            return not_found(resource, str(error))
    return ApiError(500, ErrorCode.INTERNAL, "The store failed to answer.")


# --- handlers -----------------------------------------------------------------------------


async def _api_error(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ApiError)
    return exc.response()


async def _ingest_error(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, IngestError)
    return from_ingest_error(exc).response()


async def _store_error(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StoreError)
    return from_store_error(exc).response()


async def _validation_error(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    errors: list[Mapping[str, Any]] = list(exc.errors())
    message = "The request is invalid."
    if errors:
        first = errors[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        message = f"The request is invalid ({where}): {first.get('msg', '')}".replace(" ()", "")
    problems: list[JsonValue] = [_problem(error) for error in errors]
    return error_response(422, ErrorCode.INVALID_REQUEST, message, details={"errors": problems})


def _problem(error: Mapping[str, Any]) -> JsonValue:
    """One validation problem, without echoing the offending input back."""
    return {
        "loc": [part if isinstance(part, int) else str(part) for part in error.get("loc", ())],
        "message": str(error.get("msg", "")),
        "type": str(error.get("type", "")),
    }


async def _http_error(request: Request, exc: Exception) -> Response:
    """Routing errors (unknown path, wrong method) and other HTTP exceptions."""
    assert isinstance(exc, StarletteHTTPException)
    headers = dict(exc.headers or {})
    if exc.status_code == 404:
        return error_response(
            404,
            ErrorCode.NOT_FOUND,
            f"No endpoint matches {request.method} {request.url.path}.",
            details={"resource": "endpoint"},
            headers=headers,
        )
    if exc.status_code == 405:
        message = f"{request.method} is not allowed on {request.url.path}."
    elif isinstance(exc.detail, str) and exc.detail:
        message = exc.detail
    else:
        message = HTTPStatus(exc.status_code).phrase
    code = ErrorCode.INVALID_REQUEST if 400 <= exc.status_code < 500 else ErrorCode.INTERNAL
    return error_response(exc.status_code, code, message, headers=headers)


def install(app: FastAPI) -> None:
    """Register the handlers that render every error in the error model."""
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(IngestError, _ingest_error)
    app.add_exception_handler(StoreError, _store_error)


# Responses every route documents in the OpenAPI spec (status → description).
COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid request"},
    429: {"model": ErrorResponse, "description": "Rate limited (see Retry-After)"},
    500: {"model": ErrorResponse, "description": "Internal error"},
}


def documented(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` for a route: the common errors plus ``statuses``."""
    described: dict[int | str, dict[str, Any]] = dict(COMMON_ERRORS)
    for status_code in statuses:
        described[status_code] = {
            "model": ErrorResponse,
            "description": HTTPStatus(status_code).phrase,
        }
    return dict(sorted(described.items(), key=lambda item: str(item[0])))
