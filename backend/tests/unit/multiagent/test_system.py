"""The whole pipeline with scripted models (MAS-1 to MAS-4): plan, explore, write, verify,
validate; the events and the trace; budgets and refusals; the registry."""

from __future__ import annotations

from typing import Any

import pytest

from repoatlas.agent.loop import StepEvent
from repoatlas.answers.schema import AnswerDraft, Budget, Claim
from repoatlas.answers.system import API_REFUSAL_TEXT, BUDGET_TEXT, DAILY_CAP_TEXT
from repoatlas.evals import registry
from repoatlas.evals.mapping import SymbolIndex, answer_output
from repoatlas.evals.registry import EvalContext
from repoatlas.llm.fake import FakeLLM, refusal
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import LLMResult, SpendCapReached, Usage
from repoatlas.multiagent.models import Plan, PlanDraft
from repoatlas.multiagent.system import NOT_VERIFIED_TEXT, STEPS, MultiAgentAnswerSystem
from repoatlas.multiagent.verifier import REVISED_HEADING, RepairSet, VerdictSet
from tests.unit.agent.helpers import (
    APP_REF,
    CREATE_APP_KEY,
    CREATE_APP_REF,
    LARGE,
    SLUGIFY_REF,
    SMALL,
    STEP_USAGE,
    call,
    done,
    turn,
)
from tests.unit.api.helpers import api_settings
from tests.unit.multiagent.helpers import (
    QUESTION,
    branch_of,
    notes,
    pipeline,
    plan,
    system,
    verdicts,
)

pytestmark = pytest.mark.anyio

BUDGET = Budget(max_cost_usd=0.75)
PRICES = PriceTable(DEFAULT_PRICES)
CALL = Usage(input_tokens=1000, output_tokens=200)  # FakeLLM's usage for a scripted reply
LARGE_CALL = PRICES.cost(CALL, LARGE)
SMALL_CALL = PRICES.cost(CALL, SMALL)
STEP = PRICES.cost(STEP_USAGE, SMALL)

PLAN = plan(
    ("How does create_app build the app?", [CREATE_APP_KEY], 4),
    ("What does slugify do?", [], 3),
)
BRANCHES: dict[str, list[Any]] = {
    "b1": [
        turn(call("search_code", query="create_app")),
        done(),
        notes(("create_app builds an App from the config.", ["E1", "E2"])),
    ],
    "b2": [
        turn(call("read_file", path="src/util.py", start=1, end=2)),
        done(),
        notes(("slugify lowercases its text.", ["E1"])),
    ],
}
C1 = Claim(
    id="c1",
    text="create_app builds an App from the config.",
    evidence_ids=["E1"],
    symbol="create_app",
)
# Seeded: the cited lines say nothing of the kind.
C2 = Claim(id="c2", text="create_app opens a database connection pool.", evidence_ids=["E1"])
C3 = Claim(id="c3", text="slugify lowercases its text.", evidence_ids=["E3"], symbol="slugify")
DRAFT = AnswerDraft(
    answer_markdown=(
        "`create_app` builds the app [c1] and opens a pool. [c2] `slugify` lowercases. [c3]"
    ),
    claims=[C1, C2, C3],
    refused=False,
)
GOOD = AnswerDraft(
    answer_markdown="`create_app` builds the app. [c1] `slugify` lowercases. [c3]",
    claims=[C1, C3],
    refused=False,
)


async def test_a_question_is_planned_explored_written_and_verified() -> None:
    script = pipeline(PLAN, BRANCHES, GOOD, [verdicts(c1="supported", c3="supported")])
    made, llm, box, sink = system(script)
    statuses: list[str] = []
    events: list[StepEvent] = []
    plans: list[Plan] = []

    async def on_status(step: str) -> None:
        statuses.append(step)

    async def on_step(event: StepEvent) -> None:
        events.append(event)

    async def on_plan(planned: Plan) -> None:
        plans.append(planned)

    result = await made.answer(
        1, QUESTION, BUDGET, on_status=on_status, on_step=on_step, on_plan=on_plan
    )

    assert not result.refused and result.refusal_kind is None
    assert result.answer_markdown == GOOD.answer_markdown  # all supported: the writer's text
    assert [claim.id for claim in result.claims] == ["c1", "c3"]
    assert [(c.claim_id, c.evidence_id, c.ref) for c in result.citations] == [
        ("c1", "E1", CREATE_APP_REF),
        ("c3", "E3", SLUGIFY_REF),
    ]
    assert statuses == list(STEPS)
    assert result.draft == GOOD and result.trace_id == sink.traces[0].id

    # The plan the caller hears is the funded one.
    (heard,) = plans
    assert [(b.id, b.sub_question, b.budget.max_steps) for b in heard.branches] == [
        ("b1", "How does create_app build the app?", 4),
        ("b2", "What does slugify do?", 3),
    ]
    assert heard.branches[0].seed_symbols == [CREATE_APP_KEY]
    explore_usd = sum(b.budget.max_cost_usd for b in heard.branches)
    assert explore_usd == pytest.approx(0.75 - LARGE_CALL - made.reserve(BUDGET))
    assert heard.branches[0].budget.max_cost_usd == pytest.approx(explore_usd * 4 / 7)

    # Branches, evidence, and tool calls.
    assert [(b.id, b.status, b.steps, b.evidence, b.findings) for b in result.branches] == [
        ("b1", "complete", 2, 2, 1),
        ("b2", "complete", 2, 1, 1),
    ]
    assert [(use.branch, use.name) for use in result.tool_uses] == [
        ("b1", "search_code"),
        ("b2", "read_file"),
    ]
    assert result.metrics.evidence_count == 3 and result.metrics.claims_total == 2
    assert [(v.claim_id, v.label, v.kept) for v in result.verification] == [
        ("c1", "supported", True),
        ("c3", "supported", True),
    ]
    # The planner's search is the context; it ran once, before the branches.
    assert box.calls[0] == ("search_code", {"query": QUESTION, "k": 10})

    # Roles: large plans, writes, and verifies; small explores and takes notes.
    roles = [(c.role, c.schema) for c in llm.calls if branch_of(c) is None]
    assert roles == [("large", PlanDraft), ("large", AnswerDraft), ("large", VerdictSet)]
    assert {c.role for c in llm.calls if branch_of(c) is not None} == {"small"}

    # Cost: per stage, adding up to the answer's cost.
    assert result.stage_costs == pytest.approx(
        {
            "planner": LARGE_CALL,
            "explorers": 2 * (2 * STEP + SMALL_CALL),
            "writer": LARGE_CALL,
            "verifier": LARGE_CALL,
        }
    )
    assert result.cost_usd == pytest.approx(sum(result.stage_costs.values()))
    assert result.usage.input_tokens == 3 * 1000 + 2 * 3000

    # Events: numbered through the whole answer, the plan first, then the branches' own
    # events (carrying their branch), then write and verify; their costs add up.
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert (events[0].kind, events[0].arguments, events[0].branch) == ("plan", "2 branches", None)
    assert [(e.kind, e.branch) for e in events[-2:]] == [("write", None), ("verify", None)]
    for name in ("b1", "b2"):
        mine = [e.kind for e in events if e.branch == name]
        assert mine == ["tool", "finish", "notes"]
    assert sum(event.cost_usd for event in events) == pytest.approx(result.cost_usd)

    # The trace: one span per stage and per branch, with the branch's spans inside its own.
    spans = sink.traces[0].spans
    names = [span.name for span in spans]
    for expected in ("answer", "multiagent.context", "multiagent.plan", "multiagent.write"):
        assert names.count(expected) == 1
    assert names.count("multiagent.branch") == 2 and names.count("multiagent.notes") == 2
    assert names.count("multiagent.verify") == 1 and names.count("validate") == 2
    branch_spans = {
        span.attrs["branch"]: span for span in spans if span.name == "multiagent.branch"
    }
    assert branch_spans["b1"].attrs["status"] == "complete"
    parents = {span.id: span.parent_id for span in spans}
    for span in spans:
        if span.name.startswith("tool."):
            step = parents[span.id]
            assert step is not None and parents[step] in {s.id for s in branch_spans.values()}
    root = next(span for span in spans if span.name == "answer")
    assert root.attrs["system"] == "multiagent" and root.attrs["branches"] == 2


async def test_the_verifier_drops_the_seeded_unsupported_claim_before_the_answer_is_shown() -> None:
    script = pipeline(
        PLAN, BRANCHES, DRAFT, [verdicts(c1="supported", c2="unsupported", c3="supported")]
    )
    made, _, _, sink = system(script)
    result = await made.answer(1, QUESTION, BUDGET)
    assert not result.refused
    assert [claim.id for claim in result.claims] == ["c1", "c3"]
    assert {citation.claim_id for citation in result.citations} == {"c1", "c3"}
    # The statement is gone from the text, not just its marker.
    assert result.answer_markdown == (
        f"{REVISED_HEADING}\n\n"
        "- create_app builds an App from the config. [c1]\n"
        "- slugify lowercases its text. [c3]"
    )
    assert "pool" not in result.answer_markdown
    # The label is stored with the answer; the draft keeps what the writer wrote.
    assert [(v.claim_id, v.label, v.kept) for v in result.verification] == [
        ("c1", "supported", True),
        ("c2", "unsupported", False),
        ("c3", "supported", True),
    ]
    assert result.draft == DRAFT
    root = next(span for span in sink.traces[0].spans if span.name == "answer")
    assert root.attrs["claims_unverified"] == 1 and root.attrs["claims"] == 2


async def test_a_partial_claim_is_repaired_inside_the_pipeline() -> None:
    wide = C3.model_copy(update={"text": "slugify lowercases its text and strips accents."})
    draft = DRAFT.model_copy(update={"claims": [C1, wide]})
    script = pipeline(
        PLAN,
        BRANCHES,
        draft,
        [verdicts(c1="supported", c3="partial"), verdicts(c3="supported")],
        repair={
            "claims": [
                {
                    "claim_id": "c3",
                    "text": "slugify lowercases its text.",
                    "evidence_ids": ["E3"],
                    "symbol": "slugify",
                }
            ]
        },
    )
    made, llm, _, _ = system(script)
    events: list[StepEvent] = []

    async def on_step(event: StepEvent) -> None:
        events.append(event)

    result = await made.answer(1, QUESTION, BUDGET, on_step=on_step)
    assert [claim.text for claim in result.claims] == [C1.text, "slugify lowercases its text."]
    assert "accents" not in result.answer_markdown
    assert [(v.claim_id, v.repaired, v.relabel) for v in result.verification] == [
        ("c1", False, None),
        ("c3", True, "supported"),
    ]
    assert [e.kind for e in events[-4:]] == ["write", "verify", "repair", "verify"]
    assert [c.schema for c in llm.calls[-3:]] == [VerdictSet, RepairSet, VerdictSet]
    assert result.stage_costs["verifier"] == pytest.approx(3 * LARGE_CALL)


async def test_an_answer_with_no_supported_claim_is_withheld() -> None:
    script = pipeline(PLAN, BRANCHES, DRAFT, [verdicts(c1="unsupported", c2="unsupported")])
    made, _, _, _ = system(script)
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert result.answer_markdown == NOT_VERIFIED_TEXT and result.claims == []
    assert result.refusal_reason is not None and "none of its 3 claims" in result.refusal_reason
    # c3 got no label at all: that counts as unsupported.
    assert [(v.claim_id, v.label, v.kept) for v in result.verification] == [
        ("c1", "unsupported", False),
        ("c2", "unsupported", False),
        ("c3", "unsupported", False),
    ]
    assert result.draft == DRAFT and len(result.branches) == 2 and result.cost_usd > 0


async def test_citations_are_checked_before_and_after_verification() -> None:
    bad = Claim(id="c4", text="There is a cache.", evidence_ids=["E9"])  # no such evidence
    draft = DRAFT.model_copy(update={"claims": [C1, bad]})
    # The rewrite names a symbol its cited lines don't contain: the last check removes it.
    script = pipeline(
        PLAN,
        BRANCHES,
        draft,
        [verdicts(c1="partial"), verdicts(c1="supported")],
        repair={
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "create_app exists.",
                    "evidence_ids": ["E1"],
                    "symbol": "slugify",
                }
            ]
        },
    )
    made, llm, _, _ = system(script)
    result = await made.answer(1, QUESTION, BUDGET)
    # c4 never reached the verifier (it cited nothing valid), and c1's rewrite failed the
    # validator, so nothing is left.
    first_check: Any = llm.calls[-3].messages[1]["content"][1]["text"]
    assert 'id="c4"' not in first_check and 'id="c1"' in first_check
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert result.metrics.claims_total == 2 and result.metrics.claims_dropped == 1
    assert result.metrics.prefilter_citation_validity == 0.5


async def test_the_models_own_refusal_and_the_api_refusals() -> None:
    declined = AnswerDraft(
        answer_markdown="The indexed code doesn't show this.",
        claims=[],
        refused=True,
        refusal_reason="Nothing about deployment was found.",
    )
    made, llm, _, _ = system(pipeline(PLAN, BRANCHES, declined))
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert result.answer_markdown == "The indexed code doesn't show this."
    assert result.refusal_reason == "Nothing about deployment was found."
    assert not any(c.schema is VerdictSet for c in llm.calls)  # nothing to verify

    for script in (
        pipeline(refusal(), BRANCHES),
        pipeline(PLAN, BRANCHES, refusal()),
        pipeline(PLAN, BRANCHES, GOOD, [refusal()]),
    ):
        made, _, _, _ = system(script)
        result = await made.answer(1, QUESTION, BUDGET)
        assert (result.refused, result.refusal_kind) == (True, "api")
        assert result.answer_markdown == API_REFUSAL_TEXT and result.citations == []


async def test_a_budget_too_small_for_the_pipeline_makes_no_call() -> None:
    made, llm, box, _ = system(pipeline(PLAN, BRANCHES, GOOD))
    assert 0.2 < made.reserve(BUDGET) < 0.3
    result = await made.answer(1, QUESTION, Budget(max_cost_usd=0.25))
    assert (result.refused, result.refusal_kind) == (True, "budget")
    assert result.answer_markdown == BUDGET_TEXT and llm.calls == []
    assert [name for name, _ in box.calls] == ["search_code"]  # only the context search
    assert result.cost_usd == 0.0 and result.stage_costs == {}


async def test_a_claim_is_never_shown_when_verifying_does_not_fit_the_budget() -> None:
    # The writer's call turns out far dearer than anything the reserve foresaw.
    dear = LLMResult(
        content=GOOD.model_dump_json(),
        parsed=GOOD,
        usage=Usage(input_tokens=1000, output_tokens=200),
        cost_usd=0.70,
        stop_reason="end_turn",
        blocks=[{"type": "text", "text": GOOD.model_dump_json()}],
        model=LARGE,
    )
    made, llm, _, _ = system(pipeline(PLAN, BRANCHES, dear))
    result = await made.answer(1, QUESTION, BUDGET)
    assert (result.refused, result.refusal_kind) == (True, "budget")
    assert result.answer_markdown == BUDGET_TEXT and result.claims == []
    assert not any(c.schema is VerdictSet for c in llm.calls)
    assert result.draft == GOOD and result.verification == []


async def test_the_daily_cap_stops_the_pipeline_at_any_stage() -> None:
    cap = SpendCapReached(5.0, 5.0)
    for script, spent in (
        (pipeline(cap, BRANCHES), False),
        (pipeline(PLAN, BRANCHES, cap), True),
        (pipeline(PLAN, BRANCHES, GOOD, [cap]), True),
    ):
        made, _, _, _ = system(script)
        result = await made.answer(1, QUESTION, BUDGET)
        assert (result.refused, result.refusal_kind) == (True, "budget")
        assert result.answer_markdown == DAILY_CAP_TEXT
        assert (result.cost_usd > 0) is spent


async def test_a_plan_without_branches_explores_the_question_itself() -> None:
    script = pipeline(
        plan(),
        {"b1": BRANCHES["b1"]},
        GOOD.model_copy(update={"claims": [C1]}),
        [verdicts(c1="supported")],
    )
    made, _, _, _ = system(script)
    result = await made.answer(1, QUESTION, BUDGET)
    assert not result.refused
    assert [(b.id, b.sub_question, b.max_steps) for b in result.branches] == [("b1", QUESTION, 12)]
    # The explorer repeats the planner's search and gets it from the cache.
    again = pipeline(
        plan(),
        {"b1": [turn(call("search_code", query=QUESTION, k=10)), done(), notes()]},
        GOOD.model_copy(update={"claims": [C1]}),
        [verdicts(c1="supported")],
    )
    made, _, box, _ = system(again)
    result = await made.answer(1, QUESTION, BUDGET)
    assert [name for name, _ in box.calls] == ["search_code"]
    assert [use.cached for use in result.tool_uses] == [True]
    assert [c.ref for c in result.citations] == [CREATE_APP_REF] and APP_REF is not None


def test_the_system_is_registered_and_describes_its_configuration() -> None:
    made, _, _, _ = system(pipeline(PLAN, BRANCHES))
    config = made.config()
    assert config["kind"] == "multiagent"
    assert (config["plan_model"], config["explore_model"]) == (LARGE, SMALL)
    assert (config["write_model"], config["verify_model"]) == (LARGE, LARGE)
    assert config["limits"]["max_branches"] == 4 and config["limits"]["total_steps"] == 24
    assert len(config["prompts_sha256"]) == 64 and len(config["schemas_sha256"]) == 64
    other, _, _, _ = system(pipeline(PLAN, BRANCHES), total_steps=12)
    assert other.config()["limits"] != config["limits"]
    assert "multiagent" in registry.answer_system_names()
    factory = registry.get_answer_system("multiagent")
    with pytest.raises(ValueError, match="needs an LLM client"):
        factory(EvalContext(settings=api_settings()))
    with pytest.raises(ValueError, match="needs the database"):
        factory(EvalContext(settings=api_settings(), llm=FakeLLM([])))
    assert MultiAgentAnswerSystem.name == "multiagent"


async def test_the_result_maps_to_an_evaluation_output_with_every_branchs_tool_calls() -> None:
    script = pipeline(PLAN, BRANCHES, GOOD, [verdicts(c1="supported", c3="supported")])
    made, _, _, _ = system(script)
    result = await made.answer(1, QUESTION, BUDGET)
    output = answer_output(result, SymbolIndex([]), 0.5)
    assert output.outcome == "answered"
    assert [call.name for call in output.tool_calls] == ["search_code", "read_file"]
    assert output.cost_usd == pytest.approx(result.cost_usd) and output.evidence_count == 3
