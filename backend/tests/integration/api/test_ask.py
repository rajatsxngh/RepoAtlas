"""The ask endpoint (SSE), stored runs, and traces, with ``FakeLLM`` on a real snapshot."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import AnswerDraft, Claim
from repoatlas.answers.system import OneShotAnswerSystem
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.api.app import API_PREFIX
from repoatlas.llm.fake import FakeCall, FakeLLM
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import TransientLLMError
from repoatlas.retrieval.retriever import Retriever
from repoatlas.tracing.store import SqlTraceSink
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.api.helpers import client_for, error_of

pytestmark = pytest.mark.anyio
QUESTION = "How does the cart compute its total?"


def events(body: str) -> list[tuple[str, Any]]:
    parsed = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def first_evidence(call: FakeCall) -> str:
    """The text of E1 in the prompt."""
    text_block: str = call.messages[1]["content"][0]["text"]
    return text_block.split('<evidence id="E1"', 1)[1].split("</evidence>", 1)[0]


def drafting(call: FakeCall) -> AnswerDraft:
    """A draft with one valid claim (on E1) and one citing evidence that wasn't given."""
    assert "def total" in first_evidence(call)
    return AnswerDraft(
        answer_markdown="The cart sums its items [c1]. It caches totals [c2].",
        claims=[
            Claim(id="c1", text="The cart sums its items.", evidence_ids=["E1"], symbol="total"),
            Claim(id="c2", text="It caches totals.", evidence_ids=["E99"]),
        ],
        refused=False,
    )


def install(app: FastAPI, llm: FakeLLM, min_score: float = 0.0) -> None:
    engine: Engine = app.state.engine
    app.state.answer_systems["oneshot"] = OneShotAnswerSystem(
        Retriever(app.state.store, None),
        llm,
        CitationValidator(StoreBlobs(engine)),
        RefusalPolicy(min_retrieval_score=min_score),
        prices=PriceTable(DEFAULT_PRICES),
        sink=SqlTraceSink(engine),
    )


async def ask(app: FastAPI, snapshot_id: int, question: str = QUESTION) -> Any:
    async with client_for(app) as client:
        return await client.post(
            f"{API_PREFIX}/snapshots/{snapshot_id}/ask", json={"question": question}
        )


async def test_ask_streams_status_then_a_validated_answer(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM(lambda call: drafting(call)))
    response = await ask(app, snapshot_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    stream = events(response.text)
    assert [name for name, _ in stream] == ["status", "status", "status", "answer", "done"]
    assert [data["step"] for name, data in stream if name == "status"] == [
        "retrieving",
        "answering",
        "validating",
    ]
    answer = stream[3][1]
    done = stream[4][1]
    assert answer["refused"] is False
    assert [claim["id"] for claim in answer["claims"]] == ["c1"]
    assert answer["answer_markdown"] == "The cart sums its items [c1]. It caches totals."
    (citation,) = answer["citations"]
    assert citation["evidence_id"] == "E1"
    assert len(citation["ref"]["blob_oid"]) == 40
    assert answer["metrics"]["prefilter_citation_validity"] == 0.5
    assert answer["metrics"]["dropped_claim_rate"] == 0.5
    assert "draft" not in answer
    assert answer["run_id"] == done["run_id"]
    assert answer["trace_id"] == done["trace_id"]

    async with client_for(app) as client:
        run = (await client.get(f"{API_PREFIX}/qa/{done['run_id']}")).json()
        trace = (await client.get(f"{API_PREFIX}/traces/{done['trace_id']}")).json()
    assert run["question"] == QUESTION
    assert run["mode"] == "oneshot"
    assert run["answer"]["citations"] == answer["citations"]
    assert trace["status"] == "ok"
    assert trace["snapshot_id"] == snapshot_id
    assert [span["name"] for span in trace["spans"]] == ["answer", "retrieve", "validate"]

    with app.state.engine.connect() as connection:
        row = connection.execute(
            text("SELECT refused, refusal_kind, dropped_claims, trace_id, answer FROM qa_runs")
        ).one()
    assert (row.refused, row.refusal_kind, row.dropped_claims) == (False, None, 1)
    assert str(row.trace_id) == done["trace_id"]
    assert row.answer["draft"]["claims"][1]["id"] == "c2"  # the draft is kept for analysis


async def test_weak_evidence_is_refused_and_stored(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    llm = FakeLLM([])
    install(app, llm, min_score=10.0)
    stream = events((await ask(app, snapshot_id)).text)
    answer = dict(stream)["answer"]
    assert (answer["refused"], answer["refusal_kind"]) == (True, "evidence")
    assert llm.calls == []
    with app.state.engine.connect() as connection:
        kind = connection.execute(text("SELECT refusal_kind FROM qa_runs")).scalar_one()
    assert kind == "evidence"


async def test_model_failures_become_an_error_event(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM([TransientLLMError("overloaded", status=529)]))
    stream = events((await ask(app, snapshot_id)).text)
    assert [name for name, _ in stream] == ["status", "status", "error"]
    error = stream[-1][1]["error"]
    assert error["code"] == "answer_failed"
    assert "overloaded" not in error["message"]


async def test_answers_need_an_api_key(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    response = await ask(app, snapshot_id)
    assert response.status_code == 503
    assert error_of(response)["code"] == "llm_unavailable"


async def test_unknown_snapshots_and_bad_questions(app: FastAPI) -> None:
    install(app, FakeLLM([]))
    assert (await ask(app, 999_999)).status_code == 404
    snapshot_id = store_fixture(app.state.engine, "py_small")
    response = await ask(app, snapshot_id, question="   ")
    assert response.status_code == 422
    assert error_of(response)["code"] == "invalid_request"
    async with client_for(app) as client:
        long = await client.post(
            f"{API_PREFIX}/snapshots/{snapshot_id}/ask", json={"question": "x" * 2001}
        )
        mode = await client.post(
            f"{API_PREFIX}/snapshots/{snapshot_id}/ask", json={"question": "q", "mode": "agent"}
        )
    assert long.status_code == 422
    assert mode.status_code == 422


async def test_missing_runs_and_traces_are_404(app: FastAPI) -> None:
    async with client_for(app) as client:
        run = await client.get(f"{API_PREFIX}/qa/123456")
        trace = await client.get(f"{API_PREFIX}/traces/{uuid.uuid4()}")
        bad = await client.get(f"{API_PREFIX}/traces/not-a-uuid")
    assert (run.status_code, error_of(run)["details"]["resource"]) == (404, "qa_run")
    assert (trace.status_code, error_of(trace)["details"]["resource"]) == (404, "trace")
    assert bad.status_code == 422
