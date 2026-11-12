"""Impact over HTTP (IMP-6): symbol and diff targets, stored runs, traces, errors; and the
store-backed engine answering exactly like the in-memory one."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.impact.data import MemoryImpactData
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.runs import load_impact_run
from repoatlas.impact.schema import DiffTarget, SymbolTarget
from repoatlas.impact.store_data import StoreImpactData
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import begin_snapshot, save_snapshot
from tests.integration.conftest import empty_store
from tests.integration.store.helpers import FINGERPRINT, SHA_A, SHA_B, build_files, repo_meta
from tests.unit.api.helpers import api_settings, client_for, error_of
from tests.unit.impact.helpers import (
    BASE_RUN,
    CHILD_RUN,
    MAIN,
    PARSE,
    PROJECT,
    TEST_PARSE,
    TOKENIZE,
)

pytestmark = pytest.mark.anyio
DIFF = (
    "diff --git a/pkg/core.py b/pkg/core.py\n--- a/pkg/core.py\n+++ b/pkg/core.py\n"
    "@@ -2 +2 @@\n-    return text.split()\n+    return text.split(' ')\n"
)


@dataclass(frozen=True)
class Setup:
    app: FastAPI
    engine: Engine
    snapshot: int
    pending: int
    memory: MemoryImpactData


@pytest.fixture(scope="module")
def setup(
    pg_engine: Engine, pg_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Setup]:
    built = build_files({path: body.encode() for path, body in PROJECT.items()})
    snapshot = save_snapshot(
        repo_meta("acme", "impact"),
        SHA_A,
        FINGERPRINT,
        built.inventory,
        built.blobs,
        built.graph,
        engine=pg_engine,
    )
    pending = begin_snapshot(repo_meta("acme", "later"), SHA_B, FINGERPRINT, engine=pg_engine).id
    data_dir = Path(tmp_path_factory.mktemp("impact")) / "data"
    app = create_app(api_settings(database_url=pg_url, data_dir=data_dir))
    try:
        yield Setup(
            app=app,
            engine=pg_engine,
            snapshot=snapshot,
            pending=pending,
            memory=MemoryImpactData(built.graph, PROJECT, snapshot_id=snapshot),
        )
    finally:
        app.state.engine.dispose()
        empty_store(pg_engine)


async def post(setup: Setup, body: dict[str, Any], snapshot: int | None = None) -> Any:
    sid = setup.snapshot if snapshot is None else snapshot
    async with client_for(setup.app) as client:
        return await client.post(f"{API_PREFIX}/snapshots/{sid}/impact", json=body)


@pytest.mark.parametrize(
    "target",
    [
        SymbolTarget(symbol_key=TOKENIZE),
        SymbolTarget(symbol_key=PARSE),
        SymbolTarget(symbol_key=BASE_RUN),
        DiffTarget(diff=DIFF),
    ],
)
def test_the_store_engine_answers_like_the_memory_engine(
    setup: Setup, target: SymbolTarget | DiffTarget
) -> None:
    stored = ImpactEngine(StoreImpactData(GraphStore(setup.engine)), clock=lambda: 0.0)
    memory = ImpactEngine(setup.memory, clock=lambda: 0.0)
    assert stored.analyze(setup.snapshot, target) == memory.analyze(setup.snapshot, target)


async def test_a_symbol_target_returns_a_ranked_grouped_report_and_stores_the_run(
    setup: Setup,
) -> None:
    response = await post(setup, {"symbol_key": TOKENIZE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["targets"] == [TOKENIZE]
    assert [item["symbol_key"] for item in body["items"]] == [PARSE, MAIN, TEST_PARSE]
    assert {item["group"] for item in body["items"]} == {"public_api", "caller", "test"}
    assert body["items"][0]["evidence"][0]["path"] == "pkg/core.py"
    assert {u["reason"] for u in body["unknown"]} == {"not_found", "string_literal"}
    run = load_impact_run(setup.engine, body["run_id"])
    assert run is not None and run.snapshot_id == setup.snapshot
    assert run.report.targets == [TOKENIZE]
    assert str(run.trace_id) == body["trace_id"]
    async with client_for(setup.app) as client:
        trace = (await client.get(f"{API_PREFIX}/traces/{body['trace_id']}")).json()
    assert trace["kind"] == "impact"
    assert [span["name"] for span in trace["spans"]] == ["impact.analyze"]
    with setup.engine.connect() as connection:
        target = connection.execute(
            text("SELECT target FROM impact_runs WHERE id = :id"), {"id": body["run_id"]}
        ).scalar_one()
    assert target == {"kind": "symbol", "symbol_key": TOKENIZE}


async def test_a_diff_target_and_params(setup: Setup) -> None:
    response = await post(setup, {"diff": DIFF, "params": {"max_depth": 1}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["targets"] == [TOKENIZE]
    assert [item["symbol_key"] for item in body["items"]] == [PARSE]
    assert body["params"]["max_depth"] == 1
    method = (await post(setup, {"symbol_key": BASE_RUN})).json()
    assert CHILD_RUN in [item["symbol_key"] for item in method["items"]]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"symbol_key": TOKENIZE, "diff": DIFF},
        {"symbol_key": ""},
        {"symbol_key": "a\x00b"},
        {"diff": "x" * 70_000},
        {"symbol_key": TOKENIZE, "params": {"decay": 0}},
        {"symbol_key": TOKENIZE, "params": {"edge_kinds": ["contains2"]}},
        {"symbol_key": TOKENIZE, "extra": 1},
    ],
)
async def test_invalid_requests_are_422(setup: Setup, body: dict[str, Any]) -> None:
    response = await post(setup, body)
    assert response.status_code in (413, 422), response.text
    assert error_of(response)["code"] == "invalid_request"


async def test_unknown_symbols_and_snapshots_are_404_and_unready_ones_409(setup: Setup) -> None:
    missing = await post(setup, {"symbol_key": "py:pkg/core.py:nope:function"})
    assert missing.status_code == 404
    assert error_of(missing)["details"]["resource"] == "node"
    assert (await post(setup, {"symbol_key": TOKENIZE}, snapshot=999_999)).status_code == 404
    unready = await post(setup, {"symbol_key": TOKENIZE}, snapshot=setup.pending)
    assert unready.status_code == 409
    assert error_of(unready)["code"] == "snapshot_not_ready"
