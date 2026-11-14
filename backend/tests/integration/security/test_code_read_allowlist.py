"""Every code read goes through the allowlist (GRD-7): the store's reads, the blob reads behind
citation validation, and the API's ``/code``, ``/locate``, and node owners."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, event

from repoatlas.answers.validate import StoreBlobs
from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.store.errors import InvalidQuery, PathNotInSnapshot, TextNotStored
from repoatlas.store.graphstore import GraphStore
from tests.integration.conftest import empty_store
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.api.helpers import api_settings, client_for, error_of
from tests.unit.guardrails.test_allowlist import HOSTILE_PATHS

pytestmark = pytest.mark.injection_static


@dataclass
class Stored:
    engine: Engine
    store: GraphStore
    app: FastAPI
    first: int  # py_small
    second: int  # ts_small


@pytest.fixture(scope="module")
def stored(
    pg_engine: Engine, pg_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Stored]:
    empty_store(pg_engine)
    data = Path(tmp_path_factory.mktemp("allowlist")) / "data"
    app = create_app(api_settings(database_url=pg_url, data_dir=data))
    yield Stored(
        engine=pg_engine,
        store=GraphStore(pg_engine),
        app=app,
        first=store_fixture(pg_engine, "py_small"),
        second=store_fixture(pg_engine, "ts_small"),
    )
    app.state.engine.dispose()
    empty_store(pg_engine)


class Statements:
    """SQL statements the engine runs while the block is open."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.seen: list[str] = []

    def _record(self, _conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        self.seen.append(statement)

    def __enter__(self) -> Statements:
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)

    def touching(self, table: str) -> list[str]:
        return [statement for statement in self.seen if f" {table} " in f" {statement} "]


def test_reads_return_only_files_of_their_own_snapshot(stored: Stored) -> None:
    code = stored.store.read_code(stored.first, "src/shop/cart.py", 1, 5)
    assert code.path == "src/shop/cart.py" and code.start == 1 and code.end == 5
    # The same path in another snapshot is that snapshot's business: here it has no such file.
    with pytest.raises(PathNotInSnapshot):
        stored.store.read_code(stored.second, "src/shop/cart.py", 1, 5)
    with pytest.raises(PathNotInSnapshot):
        stored.store.locate(stored.second, "src/shop/cart.py", 1)
    with pytest.raises(PathNotInSnapshot):
        stored.store.owners(stored.second, "src/shop/cart.py")


@pytest.mark.parametrize("path", [p for p in HOSTILE_PATHS if "\x00" not in p])
def test_malformed_paths_are_refused_by_every_read_without_touching_files(
    stored: Stored, path: str
) -> None:
    for read in (
        lambda: stored.store.read_code(stored.first, path, 1, 5),
        lambda: stored.store.locate(stored.first, path, 1),
        lambda: stored.store.owners(stored.first, path),
    ):
        with Statements(stored.engine) as statements, pytest.raises(PathNotInSnapshot):
            read()
        assert statements.touching("files") == [] and statements.touching("blobs") == []


def test_line_ranges_go_through_the_validator(stored: Stored) -> None:
    whole = stored.store.read_code(stored.first, "src/shop/cart.py", -50, 10_000)
    assert whole.start == 1 and whole.end == whole.line_count
    with pytest.raises(InvalidQuery, match="ends before it starts"):
        stored.store.read_code(stored.first, "src/shop/cart.py", 9, 3)
    with pytest.raises(InvalidQuery, match="past the end"):
        stored.store.read_code(stored.first, "src/shop/cart.py", whole.line_count + 1, 10_000)
    capped = GraphStore(stored.engine, max_read_lines=4).read_code(
        stored.first, "src/shop/cart.py", 2, 500
    )
    assert (capped.start, capped.end) == (2, 5)


def test_blob_reads_take_full_oids_only(stored: Stored) -> None:
    blobs = StoreBlobs(stored.engine)
    oid = stored.store.read_code(stored.first, "src/shop/cart.py", 1, 1).blob_oid
    assert "class Cart" in (blobs.text(oid) or "")
    for bad in (oid[:7], oid.upper(), oid + "' OR '1'='1", "", "../" + oid, "%"):
        with Statements(stored.engine) as statements:
            assert blobs.text(bad) is None
        assert statements.seen == []  # not even a query


def test_skipped_files_have_no_text(stored: Stored) -> None:
    with stored.engine.connect() as connection:
        from sqlalchemy import text

        skipped = (
            connection.execute(
                text("SELECT path FROM files WHERE snapshot_id = :s AND skip_reason IS NOT NULL"),
                {"s": stored.first},
            )
            .scalars()
            .first()
        )
    if skipped is None:
        pytest.skip("The fixture has no skipped file.")
    with pytest.raises(TextNotStored):
        stored.store.read_code(stored.first, skipped, 1, 5)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "../../../etc/passwd",
        "/etc/passwd",
        "src/shop/../../../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "%2e%2e/%2e%2e/etc/passwd",
        "src\\shop\\cart.py",
        "C:/Windows/win.ini",
        "src/shop/cart.py%00.png",
        "....//....//etc/passwd",
        "src/shop/./cart.py",
    ],
)
async def test_path_traversal_on_the_code_endpoint_is_refused(stored: Stored, path: str) -> None:
    async with client_for(stored.app) as client:
        for route, extra in (("code", "&start=1&end=5"), ("locate", "&line=1")):
            response = await client.get(
                f"{API_PREFIX}/snapshots/{stored.first}/{route}?path={path}{extra}"
            )
            assert response.status_code in (404, 422), (route, response.text)
            assert error_of(response)["code"] in ("invalid_request", "not_found")
            assert "root:" not in response.text and "[fonts]" not in response.text


@pytest.mark.anyio
async def test_the_code_endpoint_reads_snapshot_files(stored: Stored) -> None:
    async with client_for(stored.app) as client:
        ok = await client.get(
            f"{API_PREFIX}/snapshots/{stored.first}/code?path=src/shop/cart.py&start=1&end=3"
        )
        other = await client.get(
            f"{API_PREFIX}/snapshots/{stored.second}/code?path=src/shop/cart.py&start=1&end=3"
        )
    assert ok.status_code == 200 and ok.json()["end"] == 3
    assert other.status_code == 404
    assert error_of(other)["details"]["resource"] == "file"
