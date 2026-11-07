"""Abuse cases: path traversal in code reads, oversized bodies, build floods, and responses
that are always JSON. (Invalid, private, and oversize URLs are in test_repos.py; unknown and
not-ready snapshots in test_reads.py.)"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.store.loader import save_snapshot
from tests.integration.conftest import empty_store
from tests.integration.origin import URL, Origin
from tests.integration.store.helpers import FINGERPRINT, SHA_A, build_files, repo_meta
from tests.unit.api.helpers import api_settings, client_for, error_of

pytestmark = pytest.mark.anyio
SENTINEL = "TOP SECRET: this file lives outside every snapshot"
FILES = {
    "src/app.py": b"def main():\n    return '<script>alert(1)</script>'\n",
    "README.md": b"# Shop\n",
}


@dataclass(frozen=True)
class Target:
    app: FastAPI
    snapshot_id: int
    outside: Path


@pytest.fixture(scope="module")
def target(
    pg_engine: Engine, pg_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Target]:
    built = build_files(FILES)
    snapshot_id = save_snapshot(
        repo_meta("acme", "abuse"),
        SHA_A,
        FINGERPRINT,
        built.inventory,
        built.blobs,
        built.graph,
        engine=pg_engine,
    )
    other = build_files({"src/secret.py": b"KEY = 'other snapshot'\n"})
    save_snapshot(
        repo_meta("acme", "other"),
        SHA_A,
        FINGERPRINT,
        other.inventory,
        other.blobs,
        other.graph,
        engine=pg_engine,
    )
    root = tmp_path_factory.mktemp("abuse")
    outside = root / "outside.txt"
    outside.write_text(SENTINEL, encoding="utf-8")
    app = create_app(api_settings(database_url=pg_url, data_dir=root / "data"))
    try:
        yield Target(app=app, snapshot_id=snapshot_id, outside=outside)
    finally:
        app.state.engine.dispose()
        empty_store(pg_engine)


@pytest.fixture
async def client(target: Target) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(target.app) as http:
        yield http


TRAVERSALS_REJECTED = [
    "../etc/passwd",
    "../../../../../../../../etc/passwd",
    "/etc/passwd",
    "src/../../etc/passwd",
    "src/../src/app.py",
    "./src/app.py",
    "src/./app.py",
    "src//app.py",
    "src/app.py/",
    "src/app.py/..",
    "..",
    "file:///etc/passwd",
    "src/app.py\x00.png",
    "x" * 5_000,
]
TRAVERSALS_NOT_FOUND = [
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "..\\..\\..\\windows\\win.ini",
    "\\\\server\\share\\file.py",
    "src\\app.py",  # the real file, with Windows separators
    "SRC/APP.PY",
    "src/app.py ",
    "\uff0e\uff0e/etc/passwd",  # fullwidth dots
    "%2e%2e/etc/passwd",  # literal percent signs: decoded once, never twice
    "src/secret.py",  # in another snapshot
    "etc/passwd",
]


@pytest.mark.parametrize("path", TRAVERSALS_REJECTED)
async def test_traversal_paths_are_rejected(
    target: Target, client: httpx.AsyncClient, path: str
) -> None:
    for endpoint, params in (("code", {}), ("locate", {"line": 1})):
        response = await client.get(
            f"{API_PREFIX}/snapshots/{target.snapshot_id}/{endpoint}",
            params={"path": path, **params},
        )
        assert response.status_code == 422, (endpoint, path, response.text)
        assert error_of(response)["code"] == "invalid_request"
        assert SENTINEL not in response.text and "root:" not in response.text


@pytest.mark.parametrize("path", TRAVERSALS_NOT_FOUND)
async def test_paths_outside_the_snapshot_are_not_found(
    target: Target, client: httpx.AsyncClient, path: str
) -> None:
    for endpoint, params in (("code", {}), ("locate", {"line": 1})):
        response = await client.get(
            f"{API_PREFIX}/snapshots/{target.snapshot_id}/{endpoint}",
            params={"path": path, **params},
        )
        assert response.status_code == 404, (endpoint, path, response.text)
        assert error_of(response)["details"] == {"resource": "file"}
        assert "other snapshot" not in response.text


async def test_encoded_and_absolute_variants_never_reach_the_disk(
    target: Target, client: httpx.AsyncClient
) -> None:
    base = f"{API_PREFIX}/snapshots/{target.snapshot_id}/code?path="
    for raw, status in (
        ("%2e%2e%2fetc%2fpasswd", 422),  # ../etc/passwd once decoded
        ("..%2F..%2Foutside.txt", 422),
        ("%252e%252e%252foutside.txt", 404),  # decoded once: a strange but harmless name
        ("src%2Fapp.py%00", 422),
        (str(target.outside).replace("\\", "/"), 404 if ":" in str(target.outside) else 422),
        (str(target.outside), 404 if ":" in str(target.outside) else 422),
    ):
        response = await client.get(base + raw)
        assert response.status_code == status, (raw, response.text)
        assert SENTINEL not in response.text
    real = await client.get(base + "src%2Fapp.py")
    assert real.status_code == 200 and real.json()["path"] == "src/app.py"


async def test_responses_are_json_even_when_repo_text_looks_like_html(
    target: Target, client: httpx.AsyncClient
) -> None:
    response = await client.get(
        f"{API_PREFIX}/snapshots/{target.snapshot_id}/code", params={"path": "src/app.py"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "<script>alert(1)</script>" in response.json()["text"]  # data, never markup
    missing = await client.get(f"{API_PREFIX}/snapshots/{target.snapshot_id}/nodes/%3Cscript%3E")
    assert missing.headers["content-type"] == "application/json"


async def test_oversized_bodies_are_refused_before_they_are_read(
    target: Target, client: httpx.AsyncClient, pg_engine: Engine
) -> None:
    padded = {"url": URL, "padding": "x" * 100_000}
    declared = await client.post(f"{API_PREFIX}/repos", json=padded)
    assert declared.status_code == 413
    assert error_of(declared)["code"] == "invalid_request"
    assert error_of(declared)["details"] == {"max_bytes": 65_536}

    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: streamed
        yield b'{"url": "' + URL.encode() + b'", "padding": "'
        for _ in range(20):
            yield b"x" * 8_192
        yield b'"}'

    streamed = await client.post(
        f"{API_PREFIX}/repos", content=chunks(), headers={"content-type": "application/json"}
    )
    assert streamed.status_code == 413 and error_of(streamed)["code"] == "invalid_request"
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 0


async def test_build_floods_are_rate_limited_without_queueing_more(
    origin: Origin, pg_url: str, tmp_path: Path, pg_engine: Engine, clean_store: None
) -> None:
    app = create_app(
        api_settings(
            database_url=pg_url,
            data_dir=tmp_path / "data",
            rate_limit_enabled=True,
            builds_per_hour=2,
        )
    )
    try:
        async with client_for(app, address="192.0.2.50") as client:
            statuses = [
                (await client.post(f"{API_PREFIX}/repos", json={"url": URL})).status_code
                for _ in range(4)
            ]
            limited = await client.post(f"{API_PREFIX}/repos", json={"url": URL})
        async with client_for(app, address="192.0.2.51") as neighbor:
            other = await neighbor.post(f"{API_PREFIX}/repos", json={"url": URL})
    finally:
        app.state.engine.dispose()
    assert statuses == [202, 202, 429, 429]
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert error_of(limited)["details"]["group"] == "build"
    assert other.status_code == 202 and other.json()["reused"]
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 1
    assert len([path for path in origin.api_paths if path.endswith("/commits/main")]) == 3
