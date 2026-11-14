"""The agent answering mode on a real snapshot (AGT-3, AGT-5): real tools, ledger, validator,
and trace store; only the model is scripted."""

from __future__ import annotations

import re
from typing import Any

import pytest

from repoatlas.agent.factory import agent_budget, agent_system
from repoatlas.agent.loop import StepEvent
from repoatlas.answers.runs import load_run, save_run
from repoatlas.answers.schema import AnswerDraft, Claim
from repoatlas.llm.fake import FakeCall, FakeLLM
from repoatlas.tracing.store import load_trace
from tests.integration.agent.conftest import Built
from tests.unit.agent.helpers import call, done, turn

pytestmark = pytest.mark.anyio
ROUND = "py:src/shop/utils/helpers.py:round_money:function"
EVIDENCE = re.compile(r'<evidence id="(E\d+)" path="([^"]+)" lines="(\d+)-(\d+)"')


def seen_evidence(fake_call: FakeCall) -> dict[tuple[str, int, int], str]:
    """Ledger IDs by (path, start, end), from every tool result the call saw."""
    found: dict[tuple[str, int, int], str] = {}
    for message in fake_call.messages:
        if message["role"] != "user":
            continue
        for block in message["content"]:
            if block.get("type") == "tool_result":
                for eid, path, start, end in EVIDENCE.findall(block["content"]):
                    found[(path, int(start), int(end))] = eid
    return found


def scripted_model(fake_call: FakeCall) -> Any:
    """Search, look at the callers of ``round_money``, finish, then answer from the ledger."""
    if fake_call.schema is not None:
        ids = seen_evidence(fake_call)
        total = ids[("src/shop/cart.py", 17, 19)]
        add = ids[("src/shop/cart.py", 15, 15)]
        return AnswerDraft(
            answer_markdown="The total is the sum of the item prices [c1]. Prices are rounded "
            "when an item is added [c2]. It also emails the user [c3].",
            claims=[
                Claim(
                    id="c1",
                    text="Cart.total sums the prices.",
                    evidence_ids=[total],
                    symbol="total",
                ),
                Claim(
                    id="c2", text="add rounds each price.", evidence_ids=[add], symbol="round_money"
                ),
                Claim(id="c3", text="It emails the user.", evidence_ids=["E999"], symbol=None),
            ],
            refused=False,
        )
    turns = sum(1 for message in fake_call.messages if message["role"] == "assistant")
    if turns == 0:
        return turn(call("search_code", query="cart total price", k=3))
    if turns == 1:
        return turn(call("find_callers", symbol_key=ROUND))
    return done()


async def test_an_agent_answer_on_a_built_snapshot_is_cited_stored_and_traced(shop: Built):
    llm = FakeLLM(scripted_model)
    agent = agent_system(shop.settings, shop.engine, llm, shop.store)
    events: list[StepEvent] = []

    async def on_step(event: StepEvent) -> None:
        events.append(event)

    budget = agent_budget(shop.settings)
    result = await agent.answer(
        shop.snapshot_id, "How is the cart total computed?", budget, on_step=on_step
    )
    assert not result.refused, result.refusal_reason
    assert [claim.id for claim in result.claims] == ["c1", "c2"]  # c3 cited nothing real
    refs = {citation.claim_id: citation.ref for citation in result.citations}
    assert (refs["c1"].path, refs["c1"].start_line, refs["c1"].end_line) == (
        "src/shop/cart.py",
        17,
        19,
    )
    assert (refs["c2"].path, refs["c2"].start_line) == ("src/shop/cart.py", 15)
    assert all(len(ref.blob_oid) == 40 for ref in refs.values())
    assert result.metrics.claims_dropped == 1 and result.metrics.evidence_count >= 4
    assert [(e.kind, e.tool) for e in events] == [
        ("tool", "search_code"),
        ("tool", "find_callers"),
        ("answer", "final_answer"),
    ]
    assert events[0].evidence == 3 and events[1].evidence >= 2
    assert [use.name for use in result.tool_uses] == ["search_code", "find_callers"]

    # The run is stored like any answer, with its tool calls, and read back unchanged.
    run_id = save_run(
        shop.engine, shop.snapshot_id, "agent", "How is the cart total computed?", result
    )
    stored = load_run(shop.engine, run_id)
    assert stored is not None and stored.mode == "agent" and stored.result == result

    # Its trace has the steps, the tool calls, and the final call, in order.
    assert result.trace_id is not None
    trace = load_trace(shop.engine, result.trace_id)
    assert trace is not None and trace.kind == "answer"
    names = [span.name for span in trace.spans]
    assert names == [
        "answer",
        "agent.step",
        "tool.search_code",
        "agent.step",
        "tool.find_callers",
        "agent.step",
        "agent.answer",
        "validate",
    ]
    tool = trace.spans[2]
    assert tool.kind == "tool" and tool.attrs["arguments"] == 'query="cart total price", k=3'
    assert tool.attrs["evidence"] == 3 and tool.attrs["allowed"] is True


async def test_the_model_only_ever_sees_prepared_tool_output(shop: Built):
    llm = FakeLLM(scripted_model)
    agent = agent_system(shop.settings, shop.engine, llm, shop.store)
    await agent.answer(
        shop.snapshot_id, "How is the cart total computed?", agent_budget(shop.settings)
    )
    final = llm.calls[-1]
    assert final.role == "large" and final.tool_choice == "none"
    results = [
        block["content"]
        for message in final.messages
        if message["role"] == "user"
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    search, callers, ended = results
    assert search.startswith("Results, best first: 3.")
    assert "18 |     def total(self) -> Decimal:" in search
    assert search.count("<evidence id=") == 3 and callers.count("<evidence id=") >= 2
    assert ended == "The exploration has ended."
    # The ledger's ids run on from one result to the next.
    assert re.findall(r'id="(E\d+)"', search) == ["E1", "E2", "E3"]
    assert "E4" in re.findall(r'id="(E\d+)"', callers)


async def test_a_question_the_snapshot_cannot_answer_is_refused_without_a_final_call(shop: Built):
    def gives_up(fake_call: FakeCall) -> Any:
        turns = sum(1 for message in fake_call.messages if message["role"] == "assistant")
        if turns == 0:
            return turn(call("find_definition", name="zzz_no_such_symbol_qq"))
        return done()

    llm = FakeLLM(gives_up)
    agent = agent_system(shop.settings, shop.engine, llm, shop.store)
    result = await agent.answer(
        shop.snapshot_id, "Where is zzz defined?", agent_budget(shop.settings)
    )
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert [c.role for c in llm.calls] == ["small", "small"]
