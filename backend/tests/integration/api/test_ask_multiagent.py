"""Multi-agent mode on the ask endpoint (MAS-6): the ``plan`` event, ``step`` events that carry
their branch, the stored run with its branches and verification, and a trace grouped by
branch."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import FastAPI

from repoatlas.answers.schema import AnswerDraft, Claim
from repoatlas.api.app import API_PREFIX
from repoatlas.llm.fake import FakeCall, FakeLLM, refusal
from repoatlas.multiagent.factory import multiagent_system
from repoatlas.multiagent.models import PlanDraft
from repoatlas.multiagent.verifier import VerdictSet
from tests.integration.api.test_ask import events
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.agent.helpers import call, done, turn
from tests.unit.api.helpers import client_for, error_of
from tests.unit.multiagent.helpers import branch_of, notes, plan, turns, verdicts

pytestmark = pytest.mark.anyio
QUESTION = "How does the cart compute its total, and what does round_money do?"
_BLOCK = re.compile(r'<evidence id="(E\d+)" path="([^"]+)" lines="(\d+)-(\d+)"')
_LISTED = re.compile(r'<evidence id="(E\d+)" path="[^"]+" lines="\d+-\d+"[^>]*/>')

READS = {
    "b1": call("read_file", path="src/shop/cart.py", start=17, end=19),
    "b2": call("read_file", path="src/shop/utils/helpers.py", start=4, end=5),
}


def model(fake_call: FakeCall) -> Any:
    """Plans two branches; each reads one place and reports it; the draft has a claim for
    each, and a third that its cited lines don't support."""
    if fake_call.schema is PlanDraft:
        return plan(
            ("How does Cart.total add up the items?", [], 3),
            ("What does round_money do?", [], 2),
        )
    branch = branch_of(fake_call)
    if branch is not None:
        if fake_call.schema is not None:  # the branch's notes
            listed = [
                found
                for message in fake_call.messages
                if message["role"] == "user"
                for block in message["content"]
                if block.get("type") == "tool_result"
                for found in _LISTED.findall(block["content"])
            ]
            assert listed, "the branch was asked for notes without evidence"
            return notes((f"Branch {branch} read the code.", [listed[0]]))
        return turn(READS[branch]) if turns(fake_call) == 0 else done()
    user: Any = fake_call.messages[1]["content"]
    if fake_call.schema is AnswerDraft:
        ids = {(path, int(start)): eid for eid, path, start, _ in _BLOCK.findall(user[0]["text"])}
        cart, helpers = ids[("src/shop/cart.py", 17)], ids[("src/shop/utils/helpers.py", 4)]
        return AnswerDraft(
            answer_markdown=(
                "The cart sums its items [c1] and caches totals [c2]. Money is rounded [c3]."
            ),
            claims=[
                Claim(
                    id="c1", text="The cart sums its items.", evidence_ids=[cart], symbol="total"
                ),
                Claim(id="c2", text="The cart caches its totals.", evidence_ids=[cart]),
                Claim(
                    id="c3",
                    text="round_money rounds an amount.",
                    evidence_ids=[helpers],
                    symbol="round_money",
                ),
            ],
            refused=False,
        )
    assert fake_call.schema is VerdictSet
    return verdicts(c1="supported", c2="unsupported", c3="supported")


def install(app: FastAPI, llm: FakeLLM) -> None:
    state = app.state
    state.answer_systems["multiagent"] = multiagent_system(
        state.settings, state.engine, llm, state.store
    )


async def ask(app: FastAPI, snapshot_id: int, mode: str = "multiagent") -> Any:
    async with client_for(app) as client:
        return await client.post(
            f"{API_PREFIX}/snapshots/{snapshot_id}/ask", json={"question": QUESTION, "mode": mode}
        )


async def test_multiagent_mode_streams_the_plan_and_branch_steps_then_a_verified_answer(
    app: FastAPI,
) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM(model))
    response = await ask(app, snapshot_id)
    assert response.status_code == 200
    stream = events(response.text)
    assert [data["step"] for name, data in stream if name == "status"] == [
        "planning",
        "exploring",
        "writing",
        "verifying",
        "validating",
    ]
    assert [name for name, _ in stream][-2:] == ["answer", "done"]

    # The plan: before any branch's step.
    names = [name for name, _ in stream]
    (planned,) = (data for name, data in stream if name == "plan")
    assert planned == {
        "branches": [
            {
                "id": "b1",
                "sub_question": "How does Cart.total add up the items?",
                "seed_symbols": [],
                "max_steps": 3,
            },
            {
                "id": "b2",
                "sub_question": "What does round_money do?",
                "seed_symbols": [],
                "max_steps": 2,
            },
        ]
    }
    steps = [data for name, data in stream if name == "step"]
    assert names.index("plan") < names.index("status", names.index("plan"))
    assert [s["seq"] for s in steps] == list(range(1, len(steps) + 1))
    assert (steps[0]["kind"], steps[0]["branch"], steps[0]["arguments"]) == (
        "plan",
        None,
        "2 branches",
    )
    # Every branch's events carry its ID: a tool call, the end, the notes.
    for branch in ("b1", "b2"):
        mine = [(s["kind"], s["tool"]) for s in steps if s["branch"] == branch]
        assert mine == [("tool", "read_file"), ("finish", "final_answer"), ("notes", "notes")]
    assert [(s["kind"], s["branch"]) for s in steps[-2:]] == [("write", None), ("verify", None)]
    assert steps[-1]["model"] == "claude-sonnet-5-5"

    answer, finished = stream[-2][1], stream[-1][1]
    # The unsupported claim is gone, with its statement: the text was rebuilt from the claims.
    assert answer["refused"] is False and [c["id"] for c in answer["claims"]] == ["c1", "c3"]
    assert "caches" not in answer["answer_markdown"]
    assert answer["answer_markdown"].startswith("Verified statements about the code:")
    assert {c["ref"]["path"] for c in answer["citations"]} == {
        "src/shop/cart.py",
        "src/shop/utils/helpers.py",
    }
    assert [(v["claim_id"], v["label"], v["kept"]) for v in answer["verification"]] == [
        ("c1", "supported", True),
        ("c2", "unsupported", False),
        ("c3", "supported", True),
    ]
    assert [(b["id"], b["status"], b["steps"], b["evidence"]) for b in answer["branches"]] == [
        ("b1", "complete", 2, 1),
        ("b2", "complete", 2, 1),
    ]
    assert [(u["branch"], u["name"]) for u in answer["tool_uses"]] == [
        ("b1", "read_file"),
        ("b2", "read_file"),
    ]
    assert set(answer["stage_costs"]) == {"planner", "explorers", "writer", "verifier"}
    assert answer["cost_usd"] == pytest.approx(sum(answer["stage_costs"].values()))
    assert answer["cost_usd"] == pytest.approx(sum(s["cost_usd"] for s in steps))
    assert "draft" not in answer

    async with client_for(app) as client:
        run = (await client.get(f"{API_PREFIX}/qa/{finished['run_id']}")).json()
        trace = (await client.get(f"{API_PREFIX}/traces/{finished['trace_id']}")).json()
    assert run["mode"] == "multiagent"
    assert run["answer"]["verification"] == answer["verification"]
    assert run["answer"]["branches"] == answer["branches"]
    # The trace: each branch is one span, and a branch's steps are inside its span.
    spans = {span["id"]: span for span in trace["spans"]}
    branches = {
        span["attrs"]["branch"]: span["id"]
        for span in trace["spans"]
        if span["name"] == "multiagent.branch"
    }
    assert set(branches) == {"b1", "b2"}

    def branch_above(span_id: str) -> str | None:
        seen = spans[span_id]
        while seen["parent_id"] is not None:
            seen = spans[seen["parent_id"]]
            if seen["name"] == "multiagent.branch":
                return str(seen["attrs"]["branch"])
        return None

    for step in steps:
        if step["branch"] is not None:
            assert branch_above(step["span_id"]) == step["branch"]
    top = [span["name"] for span in trace["spans"] if span["parent_id"] == trace["spans"][0]["id"]]
    assert top.count("multiagent.branch") == 2
    for expected in ("multiagent.plan", "multiagent.write", "multiagent.verify"):
        assert expected in top


async def test_a_refusal_in_multiagent_mode_is_stored_like_any_answer(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM([refusal()]))
    stream = events((await ask(app, snapshot_id)).text)
    assert [name for name, _ in stream] == ["status", "answer", "done"]
    answer = stream[1][1]
    assert (answer["refused"], answer["refusal_kind"]) == (True, "api")
    assert answer["branches"] == [] and stream[2][1]["run_id"] >= 1


async def test_multiagent_mode_works_through_the_offline_stand_in(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real client and transport path, with no model (decisions 0020 and 0023)."""
    snapshot_id = store_fixture(app.state.engine, "py_small")
    monkeypatch.setattr(app.state.settings, "anthropic_api_key", None)
    monkeypatch.setattr(app.state.settings, "llm_transport", "offline")
    stream = events((await ask(app, snapshot_id)).text)
    (planned,) = (data for name, data in stream if name == "plan")
    assert [b["sub_question"] for b in planned["branches"]] == [QUESTION]
    steps = [data for name, data in stream if name == "step"]
    assert [(s["kind"], s["branch"]) for s in steps] == [
        ("plan", None),
        ("tool", "b1"),
        ("finish", "b1"),
        ("notes", "b1"),
        ("write", None),
        ("verify", None),
    ]
    assert steps[1]["tool"] == "search_code" and steps[1]["cached"] is False
    assert all(s["cost_usd"] == 0 for s in steps)
    answer = stream[-2][1]
    assert answer["refused"] is False and answer["cost_usd"] == 0
    assert "no model wrote this answer" in answer["answer_markdown"]
    assert 1 <= len(answer["citations"]) <= 3
    assert all(v["label"] == "supported" and v["kept"] for v in answer["verification"])
    assert "no model checked this claim" in answer["verification"][0]["reason"]
    assert [b["status"] for b in answer["branches"]] == ["complete"]


async def test_multiagent_mode_needs_an_api_key(app: FastAPI) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    response = await ask(app, snapshot_id)
    assert response.status_code == 503 and error_of(response)["code"] == "llm_unavailable"
    response = await ask(app, snapshot_id, mode="swarm")
    assert response.status_code == 422
