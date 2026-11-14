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

from repoatlas.answers.schema import AnswerDraft, Claim
from repoatlas.answers.validate import StoreBlobs
from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.impact.data import MemoryImpactData
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.explain import ImpactExplainer
from repoatlas.impact.runs import load_impact_run
from repoatlas.impact.schema import DiffTarget, SymbolTarget
from repoatlas.impact.store_data import StoreImpactData
from repoatlas.llm.fake import FakeLLM
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import TransientLLMError
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import begin_snapshot, save_snapshot
from repoatlas.tracing.store import SqlTraceSink
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


# --- explanations (GRD-1) ---------------------------------------------------------------

EXPLANATION = AnswerDraft(
    answer_markdown="Changing `_tokenize` affects `parse` [c1], tested by `test_parse` [c2]. "
    "Also `cli` [c3].",
    claims=[
        Claim(id="c1", text="parse calls _tokenize.", evidence_ids=["E2"], symbol="parse"),
        Claim(id="c2", text="test_parse calls parse.", evidence_ids=["E4"], symbol="test_parse"),
        Claim(id="c3", text="The CLI breaks.", evidence_ids=["E9"], symbol="main"),
    ],
    refused=False,
)


def install_explainer(setup: Setup, llm: FakeLLM) -> None:
    setup.app.state.impact_explainer = ImpactExplainer(
        StoreImpactData(setup.app.state.store),
        StoreBlobs(setup.engine),
        llm,
        prices=PriceTable(DEFAULT_PRICES),
        sink=SqlTraceSink(setup.engine),
    )


async def explain(setup: Setup, run_id: int, snapshot: int | None = None) -> Any:
    sid = setup.snapshot if snapshot is None else snapshot
    async with client_for(setup.app) as client:
        return await client.post(f"{API_PREFIX}/snapshots/{sid}/impact/{run_id}/explain")


async def test_explaining_a_stored_run_returns_a_cited_answer_with_its_own_trace(
    setup: Setup,
) -> None:
    llm = FakeLLM([EXPLANATION])
    install_explainer(setup, llm)
    report = (await post(setup, {"symbol_key": TOKENIZE})).json()
    response = await explain(setup, report["run_id"])
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["refused"] is False
    assert [claim["id"] for claim in answer["claims"]] == ["c1", "c2"]  # c3 cites nothing given
    assert [c["ref"]["path"] for c in answer["citations"]] == ["pkg/core.py", "tests/test_core.py"]
    assert answer["cost_usd"] > 0
    assert "draft" not in answer
    assert answer["trace_id"] != report["trace_id"]

    async with client_for(setup.app) as client:
        trace = (await client.get(f"{API_PREFIX}/traces/{answer['trace_id']}")).json()
        stored = (await client.get(f"{API_PREFIX}/qa/{answer['run_id']}")).json()
    assert trace["kind"] == "impact_explain"
    assert [span["name"] for span in trace["spans"]] == [
        "impact.explain",
        "impact.evidence",
        "validate",
    ]
    assert trace["status"] == "ok"  # (FakeLLM records no model span; LLMClient does)
    assert stored["mode"] == "impact_explain"
    assert f"`{TOKENIZE}`" in stored["question"]
    assert stored["answer"]["claims"] == answer["claims"]
    (call,) = llm.calls
    assert "<evidence id=" in call.messages[1]["content"][0]["text"]


async def test_explain_errors(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    install_explainer(setup, FakeLLM([TransientLLMError("overloaded", status=529)]))
    report = (await post(setup, {"symbol_key": TOKENIZE})).json()
    busy = await explain(setup, report["run_id"])
    assert busy.status_code == 503 and error_of(busy)["code"] == "answer_failed"

    missing = await explain(setup, 999_999)
    assert missing.status_code == 404
    assert error_of(missing)["details"]["resource"] == "impact_run"
    assert (await explain(setup, report["run_id"], snapshot=999_999)).status_code == 404
    assert (await explain(setup, report["run_id"], snapshot=setup.pending)).status_code == 409
    assert (await explain(setup, 0)).status_code == 422

    # Without an API key (and without the offline stand-in) explanations are unavailable.
    setup.app.state.impact_explainer = None
    monkeypatch.setattr(setup.app.state.settings, "anthropic_api_key", None)
    unavailable = await explain(setup, report["run_id"])
    assert unavailable.status_code == 503
    assert error_of(unavailable)["code"] == "llm_unavailable"


async def test_explanations_work_through_the_offline_stand_in(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup.app.state.impact_explainer = None
    monkeypatch.setattr(setup.app.state.settings, "anthropic_api_key", None)
    monkeypatch.setattr(setup.app.state.settings, "llm_transport", "offline")
    report = (await post(setup, {"symbol_key": TOKENIZE})).json()
    try:
        response = await explain(setup, report["run_id"])
    finally:
        setup.app.state.impact_explainer = None
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["refused"] is False and answer["cost_usd"] == 0
    assert "no model wrote this answer" in answer["answer_markdown"]
    assert len(answer["citations"]) == 3
