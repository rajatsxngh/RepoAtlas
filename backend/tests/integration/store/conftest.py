"""Store test fixtures: an emptied store, the fixture graphs, and saved read-only snapshots."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from repoatlas.graph.model import CodeGraph, Owner
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import save_snapshot
from repoatlas.store.models import STORE_TABLES
from tests.integration.store.helpers import (
    FINGERPRINT,
    SHA_A,
    Built,
    build_files,
    build_fixture,
    repo_meta,
    with_owners,
)

# Self-recursion, mutual recursion, and an import cycle.
LOOP_A = """from loop.b import pong


def ping(n):
    return pong(n - 1)


def fact(n):
    return n * fact(n - 1)
"""
LOOP_B = """from loop.a import ping


def pong(n):
    return ping(n - 1)
"""
CYCLIC = {"loop/__init__.py": b"", "loop/a.py": LOOP_A.encode(), "loop/b.py": LOOP_B.encode()}
OWNERS = {
    "src/shop/cart.py": [
        Owner(
            author_name="Ada Lovelace",
            author_hash="0123456789abcdef",
            commits=3,
            last_commit_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            share=0.75,
        ),
        Owner(
            author_name="Grace Hopper",
            author_hash="fedcba9876543210",
            commits=1,
            last_commit_at=datetime(2025, 12, 31, 23, 0, tzinfo=UTC),
            share=0.25,
        ),
    ]
}


@dataclass(frozen=True)
class Stored:
    """Three ready snapshots (``py_small`` with owners, ``ts_small``, and a cyclic one) and a
    store to read them; ``graphs`` maps each snapshot ID to the graph that was saved."""

    store: GraphStore
    py: int
    ts: int
    loop: int
    graphs: dict[int, CodeGraph]
    built: dict[int, Built]


def empty_store(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(STORE_TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def clean_store(pg_engine: Engine) -> Iterator[None]:
    """For tests that save snapshots: the loader commits its own transactions, so empty the
    store tables afterwards."""
    yield
    empty_store(pg_engine)


@pytest.fixture(scope="session")
def py_small() -> Built:
    return build_fixture("py_small")


@pytest.fixture(scope="session")
def ts_small() -> Built:
    return build_fixture("ts_small")


@pytest.fixture(scope="module")
def stored(pg_engine: Engine, py_small: Built, ts_small: Built) -> Iterator[Stored]:
    """Saved once per test module, removed when the module's tests are done."""
    py = Built(py_small.inventory, with_owners(py_small.graph, OWNERS), py_small.blobs)
    loop = build_files(CYCLIC)
    ids: dict[int, Built] = {}
    for name, built in (("py-small", py), ("ts-small", ts_small), ("loop", loop)):
        snapshot_id = save_snapshot(
            repo_meta("acme", name),
            SHA_A,
            FINGERPRINT,
            built.inventory,
            built.blobs,
            built.graph,
            engine=pg_engine,
        )
        ids[snapshot_id] = built
    try:
        py_id, ts_id, loop_id = ids
        yield Stored(
            store=GraphStore(pg_engine),
            py=py_id,
            ts=ts_id,
            loop=loop_id,
            graphs={snapshot_id: built.graph for snapshot_id, built in ids.items()},
            built=ids,
        )
    finally:
        empty_store(pg_engine)
