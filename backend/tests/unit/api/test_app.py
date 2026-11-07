"""The app skeleton without a database: liveness, request IDs, CORS, and error rendering."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI, Query

from repoatlas import __version__
from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.api.errors import ApiError, ErrorCode
from repoatlas.ingest.errors import RepoPrivate
from repoatlas.store.errors import NodeNotFound
from tests.unit.api.helpers import api_settings, client_for, error_of

pytestmark = pytest.mark.anyio
WEB = "http://localhost:3000"


@pytest.fixture
def app() -> Iterator[FastAPI]:
    app = create_app(api_settings(web_origins=f"{WEB}, http://127.0.0.1:3000/"))

    def boom() -> None:
        raise RuntimeError("secret connection details")

    def refuse() -> None:
        raise ApiError(418, ErrorCode.INVALID_REQUEST, "No tea.", details={"pot": "empty"})

    def private() -> None:
        raise RepoPrivate("acme/secret is private; only public repositories work.")

    def missing() -> None:
        raise NodeNotFound("Snapshot 1 has no node 'py:a.py:f:function'.")

    def numbers(line: int = Query(ge=1)) -> dict[str, int]:
        return {"line": line}

    for path, endpoint in (
        ("/boom", boom),
        ("/refuse", refuse),
        ("/private", private),
        ("/missing", missing),
        ("/numbers", numbers),
    ):
        app.add_api_route(API_PREFIX + "/test" + path, endpoint, methods=["GET"])
    yield app
    app.state.engine.dispose()


async def test_health_answers_without_a_database(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get(f"{API_PREFIX}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_every_response_has_a_request_id(app: FastAPI) -> None:
    async with client_for(app) as client:
        generated = await client.get(f"{API_PREFIX}/health")
        echoed = await client.get(f"{API_PREFIX}/health", headers={"X-Request-ID": "trace-42.a_b"})
        replaced = await client.get(f"{API_PREFIX}/health", headers={"X-Request-ID": "bad id!"})
        failed = await client.get(f"{API_PREFIX}/nothing")
    assert re.fullmatch(r"[0-9a-f]{32}", generated.headers["X-Request-ID"])
    assert echoed.headers["X-Request-ID"] == "trace-42.a_b"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["X-Request-ID"])
    assert re.fullmatch(r"[0-9a-f]{32}", failed.headers["X-Request-ID"])


async def test_unknown_routes_and_methods_use_the_error_model(app: FastAPI) -> None:
    async with client_for(app) as client:
        missing = await client.get(f"{API_PREFIX}/nothing")
        wrong_method = await client.post(f"{API_PREFIX}/health")
    error = error_of(missing)
    assert missing.status_code == 404
    assert error["code"] == "not_found" and error["details"] == {"resource": "endpoint"}
    assert wrong_method.status_code == 405
    assert error_of(wrong_method)["code"] == "invalid_request"
    assert wrong_method.headers["allow"] == "GET"


async def test_unexpected_exceptions_become_internal_errors_without_details(
    app: FastAPI,
) -> None:
    async with client_for(app) as client:
        response = await client.get(f"{API_PREFIX}/test/boom", headers={"Origin": WEB})
    assert response.status_code == 500
    error = error_of(response)
    assert error["code"] == "internal"
    assert "secret" not in response.text
    assert error["details"] == {"request_id": response.headers["X-Request-ID"]}
    assert response.headers["access-control-allow-origin"] == WEB  # readable by the web app


async def test_api_and_domain_errors_render_with_their_codes(app: FastAPI) -> None:
    async with client_for(app) as client:
        refused = await client.get(f"{API_PREFIX}/test/refuse")
        private = await client.get(f"{API_PREFIX}/test/private")
        missing = await client.get(f"{API_PREFIX}/test/missing")
    assert refused.status_code == 418
    assert error_of(refused) == {
        "code": "invalid_request",
        "message": "No tea.",
        "details": {"pot": "empty"},
    }
    assert private.status_code == 403 and error_of(private)["code"] == "repo_private"
    assert missing.status_code == 404
    assert error_of(missing)["details"] == {"resource": "node"}


async def test_validation_errors_name_the_parameter_without_echoing_input(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get(
            f"{API_PREFIX}/test/numbers", params={"line": "<script>alert(1)</script>"}
        )
        low = await client.get(f"{API_PREFIX}/test/numbers", params={"line": "0"})
    assert response.status_code == 422
    error = error_of(response)
    assert error["code"] == "invalid_request"
    assert error["message"].startswith("The request is invalid (query.line): ")
    assert error["details"]["errors"][0]["loc"] == ["query", "line"]
    assert "<script>" not in response.text
    assert low.status_code == 422 and "greater than or equal to 1" in error_of(low)["message"]


async def test_cors_allows_only_the_configured_web_origins(app: FastAPI) -> None:
    async with client_for(app) as client:
        allowed = await client.get(f"{API_PREFIX}/health", headers={"Origin": WEB})
        other = await client.get(
            f"{API_PREFIX}/health", headers={"Origin": "http://127.0.0.1:3000"}
        )
        evil = await client.get(f"{API_PREFIX}/health", headers={"Origin": "https://evil.example"})
        preflight = await client.options(
            f"{API_PREFIX}/health",
            headers={
                "Origin": WEB,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        rejected = await client.options(
            f"{API_PREFIX}/health",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
    assert allowed.headers["access-control-allow-origin"] == WEB
    assert "retry-after" in allowed.headers["access-control-expose-headers"].lower()
    assert other.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "access-control-allow-origin" not in evil.headers
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == WEB
    assert "access-control-allow-credentials" not in preflight.headers
    assert rejected.status_code == 400


def test_cors_origins_come_from_a_comma_separated_setting() -> None:
    assert api_settings().cors_origins == ["http://localhost:3000"]
    assert api_settings(web_origins=" https://a.example/ ,, https://b.example").cors_origins == [
        "https://a.example",
        "https://b.example",
    ]


async def test_request_bodies_are_capped(app: FastAPI) -> None:
    async def stream() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 10_000

    async with client_for(app) as client:
        declared = await client.post(f"{API_PREFIX}/repos", content=b"{" + b" " * 70_000 + b"}")
        streamed = await client.post(
            f"{API_PREFIX}/repos", content=stream(), headers={"content-type": "application/json"}
        )
        small = await client.post(f"{API_PREFIX}/repos", json={"url": "https://gitlab.com/a/b"})
    assert declared.status_code == 413
    assert error_of(declared) == {
        "code": "invalid_request",
        "message": "The request body is larger than 65,536 bytes.",
        "details": {"max_bytes": 65536},
    }
    assert streamed.status_code == 413 and error_of(streamed)["code"] == "invalid_request"
    assert small.status_code == 422 and error_of(small)["code"] == "invalid_url"
