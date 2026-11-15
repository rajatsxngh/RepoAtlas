"""Failure handling (MAS-5): a branch that fails, times out, or hits a limit gives a partial
answer that says what was not explored; only when nothing was found is there no answer."""

from __future__ import annotations

from typing import Any

import pytest

from repoatlas.agent.results import ToolResult
from repoatlas.answers.schema import AnswerDraft, Budget, Claim
from repoatlas.answers.system import API_REFUSAL_TEXT, DAILY_CAP_TEXT, NO_EVIDENCE_TEXT
from repoatlas.llm.fake import FakeCall, refusal
from repoatlas.llm.types import (
    LLMRequestError,
    OutputValidationError,
    SpendCapReached,
    TransientLLMError,
)
from repoatlas.multiagent.models import BranchNotes
from repoatlas.multiagent.system import GAPS_HEADING, STOPPED_EARLY_TEXT, gap_reason, gaps_note
from tests.unit.agent.helpers import (
    CREATE_APP_KEY,
    CREATE_APP_REF,
    SLUGIFY_REF,
    SMALL,
    FakeToolbox,
    call,
    done,
    turn,
)
from tests.unit.multiagent.helpers import (
    QUESTION,
    TimedLLM,
    branch,
    branch_of,
    by_branch,
    explorers,
    notes,
    pipeline,
    plan,
    system,
    turns,
    verdicts,
)

pytestmark = pytest.mark.anyio

BUDGET = Budget(max_cost_usd=0.75)
PLAN = plan(
    ("How does create_app build the app?", [CREATE_APP_KEY], 4),
    ("What does slugify do?", [], 3),
)
B1: list[Any] = [
    turn(call("search_code", query="create_app")),
    done(),
    notes(("create_app builds an App from the config.", ["E1"])),
]
B2: list[Any] = [
    turn(call("read_file", path="src/util.py", start=1, end=2)),
    done(),
    notes(("slugify lowercases its text.", ["E1"])),
]
C1 = Claim(
    id="c1",
    text="create_app builds an App from the config.",
    evidence_ids=["E1"],
    symbol="create_app",
)
ONE = AnswerDraft(answer_markdown="`create_app` builds the app. [c1]", claims=[C1], refused=False)
OK = [verdicts(c1="supported")]


async def test_a_branch_that_raises_does_not_fail_the_answer() -> None:
    script = pipeline(
        PLAN, {"b1": B1, "b2": [TransientLLMError("overloaded", status=529)]}, ONE, OK
    )
    made, _, _, sink = system(script)
    result = await made.answer(1, QUESTION, BUDGET)
    assert not result.refused
    assert [(b.id, b.status, b.error, b.steps) for b in result.branches] == [
        ("b1", "complete", None, 2),
        ("b2", "failed", "TransientLLMError", 0),
    ]
    # The answer is the surviving branch's, and says which part is missing.
    assert [claim.id for claim in result.claims] == ["c1"]
    assert result.answer_markdown == (
        f"`create_app` builds the app. [c1]\n\n{GAPS_HEADING}\n- What does slugify do? (it failed)"
    )
    assert [c.ref for c in result.citations] == [CREATE_APP_REF]
    # The writer was told which branch did not finish.
    spans = {
        s.attrs.get("branch"): s for s in sink.traces[0].spans if s.name == "multiagent.branch"
    }
    assert spans["b2"].attrs["status"] == "failed" and spans["b2"].status == "ok"
    assert spans["b2"].attrs["error"] == "TransientLLMError"
    assert sink.traces[0].status == "ok"


async def test_the_error_text_of_a_failed_branch_is_never_shown() -> None:
    leaky = LLMRequestError("400: prompt contained CANARY-SECRET from the repository", status=400)
    made, _, _, _ = system(pipeline(PLAN, {"b1": B1, "b2": [leaky]}, ONE, OK))
    result = await made.answer(1, QUESTION, BUDGET)
    dumped = result.model_dump_json()
    assert "CANARY-SECRET" not in dumped and result.branches[1].error == "LLMRequestError"


async def test_a_branch_that_times_out_keeps_what_it_found() -> None:
    # b2 reads the file, then its second turn hangs past the branch's time limit.
    def delay(made: FakeCall) -> float:
        return 5.0 if branch_of(made) == "b2" and turns(made) == 1 else 0.0

    both = AnswerDraft(
        answer_markdown="`create_app` builds the app. [c1] `slugify` lowercases. [c2]",
        claims=[
            C1,
            Claim(
                id="c2", text="slugify lowercases its text.", evidence_ids=["E3"], symbol="slugify"
            ),
        ],
        refused=False,
    )
    script = pipeline(PLAN, {"b1": B1, "b2": B2}, both, [verdicts(c1="supported", c2="supported")])
    llm = TimedLLM(script, delay)
    made, _, _, _ = system(None, llm=llm, branch_timeout_s=0.2)
    result = await made.answer(1, QUESTION, BUDGET)
    assert not result.refused
    second = result.branches[1]
    assert (second.status, second.stop, second.error) == ("timeout", None, None)
    assert (second.steps, second.evidence, second.findings) == (1, 1, 0)
    # Its evidence is in the merged set, so the writer could cite it.
    assert [c.ref for c in result.citations] == [CREATE_APP_REF, SLUGIFY_REF]
    assert [(use.branch, use.name) for use in result.tool_uses] == [
        ("b1", "search_code"),
        ("b2", "read_file"),
    ]
    assert result.answer_markdown.endswith(
        f"{GAPS_HEADING}\n- What does slugify do? (it ran out of time)"
    )
    # What it spent before the limit still counts.
    assert second.cost_usd > 0 and result.stage_costs["explorers"] > second.cost_usd


async def test_a_branch_stopped_by_a_limit_is_named_with_the_limit() -> None:
    endless = [
        turn(call("read_file", path="src/util.py", start=1, end=2)),
        turn(call("find_definition", name="slugify")),
        turn(call("find_callees", symbol_key=CREATE_APP_KEY)),
        notes(),
    ]
    short = plan(("How does create_app build the app?", [], 4), ("What does slugify do?", [], 3))
    made, _, _, _ = system(pipeline(short, {"b1": B1, "b2": endless}, ONE, OK))
    result = await made.answer(1, QUESTION, BUDGET)
    assert [(b.status, b.stop) for b in result.branches] == [
        ("complete", "final_answer"),
        ("partial", "max_steps"),
    ]
    assert result.answer_markdown.endswith("- What does slugify do? (it reached its step limit)")


async def test_a_branch_the_api_refused_is_named_as_declined() -> None:
    made, _, _, _ = system(pipeline(PLAN, {"b1": B1, "b2": [refusal(SMALL)]}, ONE, OK))
    result = await made.answer(1, QUESTION, BUDGET)
    assert not result.refused
    assert (result.branches[1].status, result.branches[1].error) == ("failed", "refused")
    assert result.answer_markdown.endswith("- What does slugify do? (the model declined it)")


async def test_a_branch_whose_notes_fail_still_contributes_its_evidence() -> None:
    broken = OutputValidationError("The small model's output failed validation twice.", refusal())
    made, _, _, _ = system(pipeline(PLAN, {"b1": B1, "b2": [B2[0], done(), broken]}, ONE, OK))
    result = await made.answer(1, QUESTION, BUDGET)
    second = result.branches[1]
    assert (second.status, second.error, second.evidence) == ("failed", "OutputValidationError", 1)
    assert result.metrics.evidence_count == 3 and not result.refused


async def test_when_every_branch_fails_with_an_error_the_answer_fails_with_it() -> None:
    error = TransientLLMError("overloaded", status=529)
    made, llm, _, sink = system(pipeline(PLAN, {"b1": [error], "b2": [error]}))
    with pytest.raises(TransientLLMError, match="overloaded"):
        await made.answer(1, QUESTION, BUDGET)
    assert sink.traces[0].status == "error"
    assert len(llm.calls) == 3  # the plan and one attempt per branch: no writer, no verifier


async def test_when_nothing_was_found_the_reason_decides_the_refusal() -> None:
    empty = FakeToolbox({"search_code": ToolResult(content="No results.", evidence=[])})
    nothing = [turn(call("search_code", query="deploy")), done()]

    # The tools found nothing: an evidence refusal.
    made, llm, _, _ = system(pipeline(PLAN, {"b1": nothing, "b2": nothing}), empty)
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refusal_kind, result.answer_markdown) == ("evidence", NO_EVIDENCE_TEXT)
    assert [b.status for b in result.branches] == ["complete", "complete"]
    assert all(c.schema is not AnswerDraft for c in llm.calls)

    # Limits stopped the exploring before anything was found: a budget refusal.
    looping = [turn(call("search_code", query=f"deploy {n}")) for n in range(6)]
    made, _, _, _ = system(pipeline(PLAN, {"b1": looping, "b2": nothing}), empty)
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refusal_kind, result.answer_markdown) == ("budget", STOPPED_EARLY_TEXT)
    assert result.branches[0].status == "partial"

    # Every branch was declined by the API: an api refusal.
    made, _, _, _ = system(pipeline(PLAN, {"b1": [refusal(SMALL)], "b2": [refusal(SMALL)]}))
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refusal_kind, result.answer_markdown) == ("api", API_REFUSAL_TEXT)

    # The deployment's daily cap stopped the exploring.
    cap = SpendCapReached(5.0, 5.0)
    made, _, _, _ = system(pipeline(PLAN, {"b1": [cap], "b2": [cap]}))
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refusal_kind, result.answer_markdown) == ("budget", DAILY_CAP_TEXT)
    assert [(b.status, b.error) for b in result.branches] == [("partial", "daily_cap")] * 2
    # Refusals still say what was spent and what each branch did.
    assert result.cost_usd > 0 and set(result.stage_costs) == {"planner"}


async def test_explorers_report_failures_without_raising() -> None:
    script = by_branch(
        {
            "b1": [RuntimeError("boom")],
            "b2": [turn(call("read_file", path="src/util.py", start=1, end=2)), refusal(SMALL)],
            "b3": [SpendCapReached(5.0, 5.0)],
        }
    )
    made, _, _ = explorers(script)
    one, two, three = await made.run(
        1, QUESTION, [branch("b1", "A?"), branch("b2", "B?"), branch("b3", "C?")]
    )
    assert (one.status, one.error, one.stop) == ("failed", "RuntimeError", None)
    assert isinstance(one.exception, RuntimeError) and one.notes == BranchNotes(findings=[])
    assert (two.status, two.error, len(two.ledger), two.steps) == ("failed", "refused", 1, 2)
    assert (three.status, three.error, three.exception) == ("partial", "daily_cap", None)
    assert [gap_reason(r) for r in (one, two, three)] == [
        "it failed",
        "the model declined it",
        "the server's daily limit was reached",
    ]
    assert gaps_note([one]) == f"{GAPS_HEADING}\n- A? (it failed)"


async def test_complete_branches_leave_no_gap_note() -> None:
    made, _, _ = explorers(by_branch({"b1": B1}))
    (result,) = await made.run(1, QUESTION, [branch("b1", "A?")])
    assert gap_reason(result) is None and gaps_note([result]) == ""
