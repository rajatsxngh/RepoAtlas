"""Retrieval fixtures: the fixture repos saved into the store and chunked."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine

from repoatlas.retrieval.chunker import chunk_snapshot
from repoatlas.retrieval.chunkstore import save_chunks
from repoatlas.store.loader import save_snapshot
from tests.integration.conftest import empty_store
from tests.integration.store.helpers import FINGERPRINT, SHA_A, build_fixture, repo_meta


@dataclass(frozen=True)
class Stored:
    py: int  # snapshot ID of py_small
    ts: int  # snapshot ID of ts_small


def store_fixture(engine: Engine, name: str, *, owner: str = "acme", sha: str = SHA_A) -> int:
    """Save fixture repo ``name`` as a ready snapshot and chunk it."""
    built = build_fixture(name)
    snapshot_id = save_snapshot(
        repo_meta(owner, name),
        sha,
        FINGERPRINT,
        built.inventory,
        built.blobs,
        built.graph,
        engine=engine,
    )
    save_chunks(snapshot_id, chunk_snapshot(snapshot_id, engine=engine), engine=engine)
    return snapshot_id


@pytest.fixture(scope="module")
def stored(pg_engine: Engine) -> Iterator[Stored]:
    """Both fixture repos, saved and chunked once per test module (read-only for tests)."""
    empty_store(pg_engine)
    yield Stored(py=store_fixture(pg_engine, "py_small"), ts=store_fixture(pg_engine, "ts_small"))
    empty_store(pg_engine)
