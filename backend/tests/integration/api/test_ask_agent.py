"""Agent mode on the ask endpoint (AGT-6): ``step`` events, the stored run, and its trace."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from repoatlas.agent.factory import agent_system
from repoatlas.answers.schema import AnswerDraft, Claim
from repoatlas.api.app import API_PREFIX
from repoatlas.llm.fake import FakeCall, FakeLLM, refusal
from tests.integration.agent.test_agent_answer import seen_evidence
from tests.integration.api.test_ask import events
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.agent.helpers import call, done, turn
from tests.unit.api.helpers import client_for, error_of

pytestmark = pytest.mark.anyio
QUESTION = "How does the cart compute its total?"
TOTAL = "py:src/shop/cart.py:Cart.total:method"


def model(fake_call: FakeCall) -> Any:
    """Look the method up, read it, finish, then answer from the ledger."""
    if fake_call.schema is not None:
        ids = seen_evidence(fake_call)
        return AnswerDraft(
            answer_markdown="The cart sums its items [c1]. It caches totals [c2].",
            claims=[
                Claim(
                    id="c1",
                    text="The cart sums its items.",
                    evidence_ids=[ids[("src/shop/cart.py", 17, 19)]],
                    symbol="total",
                ),
                Claim(id="c2", text="It caches totals.", evidence_ids=["E99"]),
            ],
            refused=False,
        )
    turns = sum(1 for message in fake_call.messages if message["role"] == "assistant")
    if turns == 0:
        return turn(call("find_definition", name="total"))
    if turns == 1:
        return turn(
            call("read_file", path="src/shop/cart.py", start=17, end=19),
            call("read_file", "toolu_outside", path="../../etc/passwd", start=1, end=5),
        )
    return done()


def install(app: FastAPI, llm: FakeLLM) -> None:
    state = app.state
    state.answer_systems["agent"] = agent_system(state.settings, state.engine, llm, state.store)


async def ask(app: FastAPI, snapshot_id: int, mode: str = "agent") -> Any:
    async with client_for(app) as client:
        return await client.post(
            f"{API_PREFIX}/snapshots/{snapshot_id}/ask", json={"question": QUESTION, "mode": mode}
        )


async def test_agent_mode_streams_steps_then_a_validated_answer(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM(model))
    response = await ask(app, snapshot_id)
    assert response.status_code == 200
    stream = events(response.text)
    assert [name for name, _ in stream] == [
        "status",  # exploring
        "step",  # find_definition
        "step",  # read_file
        "step",  # read_file outside the snapshot: refused
        "step",  # the model ends the exploration
        "status",  # answering
        "step",  # the final answer call
        "status",  # validating
        "answer",
        "done",
    ]
    assert [data["step"] for name, data in stream if name == "status"] == [
        "exploring",
        "answering",
        "validating",
    ]
    steps = [data for name, data in stream if name == "step"]
    assert [(s["seq"], s["step"], s["kind"], s["tool"]) for s in steps] == [
        (1, 1, "tool", "find_definition"),
        (2, 2, "tool", "read_file"),
        (3, 2, "tool", "read_file"),
        (4, 3, "finish", "final_answer"),
        (5, 4, "answer", "answer"),
    ]
    lookup, read, outside, finish, final = steps
    assert finish["tokens_in"] == 1_000 and finish["evidence"] == 0
    assert lookup["arguments"] == 'name="total"' and lookup["evidence"] == 1
    assert lookup["model"] == "claude-haiku-5-5" and lookup["tokens_in"] == 1_000
    assert lookup["cost_usd"] > 0 and lookup["result_tokens"] > 0
    assert read["allowed"] is True and read["error"] is None
    assert outside["allowed"] is False and "is not a file of this snapshot" in outside["error"]
    assert (outside["tokens_in"], outside["cost_usd"]) == (0, 0.0)  # reported once per turn
    assert final["model"] == "claude-sonnet-5-5" and final["cost_usd"] > lookup["cost_usd"]

    answer, finished = stream[-2][1], stream[-1][1]
    assert answer["refused"] is False and [c["id"] for c in answer["claims"]] == ["c1"]
    assert answer["answer_markdown"] == "The cart sums its items [c1]. It caches totals."
    (citation,) = answer["citations"]
    assert (citation["ref"]["path"], citation["ref"]["start_line"]) == ("src/shop/cart.py", 17)
    assert [(u["name"], u["allowed"]) for u in answer["tool_uses"]] == [
        ("find_definition", True),
        ("read_file", True),
        ("read_file", False),
    ]
    assert "draft" not in answer
    assert answer["cost_usd"] == pytest.approx(sum(s["cost_usd"] for s in steps))

    async with client_for(app) as client:
        run = (await client.get(f"{API_PREFIX}/qa/{finished['run_id']}")).json()
        trace = (await client.get(f"{API_PREFIX}/traces/{finished['trace_id']}")).json()
    assert run["mode"] == "agent" and run["answer"]["tool_uses"] == answer["tool_uses"]
    # Every step event names a span of the answer's trace.
    spans = {span["id"]: span for span in trace["spans"]}
    assert [spans[s["span_id"]]["name"] for s in steps] == [
        "tool.find_definition",
        "tool.read_file",
        "tool.read_file",
        "agent.step",
        "agent.answer",
    ]
    assert spans[outside["span_id"]]["attrs"]["allowed"] is False
    assert trace["cost_usd"] == 0  # FakeLLM makes no llm spans; the cost is on the answer


async def test_a_refusal_in_agent_mode_is_stored_like_any_answer(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM([refusal(model="claude-haiku-5-5")]))
    stream = events((await ask(app, snapshot_id)).text)
    assert [name for name, _ in stream] == ["status", "answer", "done"]
    answer = stream[1][1]
    assert (answer["refused"], answer["refusal_kind"]) == (True, "api")
    assert answer["tool_uses"] == [] and stream[2][1]["run_id"] >= 1


async def test_agent_mode_works_through_the_offline_stand_in(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real client and transport path, with no model (decisions 0020 and 0022)."""
    snapshot_id = store_fixture(app.state.engine, "py_small")
    monkeypatch.setattr(app.state.settings, "anthropic_api_key", None)
    monkeypatch.setattr(app.state.settings, "llm_transport", "offline")
    stream = events((await ask(app, snapshot_id)).text)
    assert [name for name, _ in stream if name != "status"] == [
        "step",
        "step",
        "step",
        "answer",
        "done",
    ]
    search, _, final = (data for name, data in stream if name == "step")
    assert search["tool"] == "search_code" and search["evidence"] >= 1
    assert search["arguments"].startswith('query="How does the cart compute its total?"')
    assert (final["kind"], final["model"], final["cost_usd"]) == ("answer", "offline-stand-in", 0)
    answer = stream[-2][1]
    assert answer["refused"] is False and answer["cost_usd"] == 0
    assert "no model wrote this answer" in answer["answer_markdown"]
    assert 1 <= len(answer["citations"]) <= 3
    assert all(c["ref"]["path"].startswith(("src/", "tests/")) for c in answer["citations"])
    async with client_for(app) as client:
        trace = (await client.get(f"{API_PREFIX}/traces/{stream[-1][1]['trace_id']}")).json()
    names = [span["name"] for span in trace["spans"]]
    assert names == [
        "answer",
        "agent.step",
        "llm.call",
        "tool.search_code",
        "agent.step",
        "llm.call",
        "agent.answer",
        "llm.call",
        "validate",
    ]


async def test_agent_mode_needs_an_api_key_and_unknown_modes_are_rejected(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    response = await ask(app, snapshot_id)
    assert response.status_code == 503 and error_of(response)["code"] == "llm_unavailable"
    response = await ask(app, snapshot_id, mode="autonomous")
    assert response.status_code == 422
    install(app, FakeLLM([]))
    assert "agent" in app.state.answer_systems and "oneshot" not in app.state.answer_systems
