"""The three-way comparison, end to end without a model (MAS-7; the M15 build gate, decision
0023).

The real pieces run: the three registered answer systems (``oneshot``, ``agent``, and
``multiagent``) answer the same questions on a built snapshot through the eval runner, three
runs each; the benchmark scorers score them; reports are written and validated; and
``repoatlas eval compare`` with the three reports prints every system's score per question type
with its 95% interval, the cost per question, the multi-agent pipeline's cost per stage, and
the paired difference of every pair.

Only the model is replaced, by scripted stand-ins. **The numbers measure the stand-ins, not the
systems**: they show that the tooling works, and nothing about whether a pipeline of agents
beats one agent or one retrieval pass. That is the live run, pending in ``docs/live-pass.md``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from repoatlas.answers.schema import AnswerDraft
from repoatlas.cli import app
from repoatlas.config import Settings
from repoatlas.evals.compare import compare_many, stage_costs
from repoatlas.evals.registry import EvalContext, register_suite
from repoatlas.evals.report import Report, build_report, read_report, validate_report, write_report
from repoatlas.evals.runner import run_eval
from repoatlas.evals.scorers import score_benchmark
from repoatlas.evals.session import EvalOptions, prepare_run, report_meta
from repoatlas.llm.fake import FakeCall, FakeLLM
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import Usage
from repoatlas.multiagent.models import BranchNotes, PlanDraft
from repoatlas.multiagent.verifier import RepairSet, VerdictSet
from tests.integration.conftest import empty_store
from tests.integration.evals.test_agent_vs_oneshot import (
    _BLOCK,
    _CALLER,
    _LISTED,
    _NAME,
    _QUESTION,
    QUESTIONS,
    agent_model,
    draft,
    load,
    one_shot_model,
    prepare,
    tool_results,
)
from tests.unit.agent.helpers import call, done, turn
from tests.unit.api.helpers import api_settings

pytestmark = pytest.mark.anyio

SUITE = "three_way_smoke"
SYSTEMS = ("oneshot", "agent", "multiagent")
_CLAIM = re.compile(r'^<claim id="(c\d+)"', re.MULTILINE)
_BRANCH = re.compile(r'<branch id="(b\d+)">\n(.*?)\n', re.DOTALL)


def multiagent_model(fake_call: FakeCall) -> Any:
    """Plans one branch for a lookup and two for a callers question (the definition, and the
    callers); each explorer asks the graph and reads what it finds; the writer answers from
    the merged evidence; the verifier accepts every claim."""
    content: Any = fake_call.messages[1]["content"]
    texts = [block["text"] for block in content if block.get("type") == "text"]
    if fake_call.schema is PlanDraft:
        question = _QUESTION.search(texts[-1])
        assert question is not None
        name = _NAME.findall(question[1])[0]
        if "Where is" in question[1]:
            return {
                "branches": [
                    {"sub_question": f"Where is `{name}` defined?", "seed_symbols": [], "steps": 2}
                ]
            }
        return {
            "branches": [
                {"sub_question": f"Where is `{name}` defined?", "seed_symbols": [], "steps": 2},
                {"sub_question": f"Which functions call `{name}`?", "seed_symbols": [], "steps": 4},
            ]
        }
    if fake_call.schema is VerdictSet:
        return {
            "verdicts": [
                {"claim_id": claim_id, "label": "supported", "reason": "The cited lines show it."}
                for claim_id in _CLAIM.findall(texts[-1])
            ]
        }
    if fake_call.schema is RepairSet:
        return {"claims": []}
    if fake_call.schema is AnswerDraft:
        return write(texts)
    branch = _BRANCH.search(texts[1])
    assert branch is not None, "an explorer was called without a branch"
    sub_question = branch[2]
    name = _NAME.findall(sub_question)[0]
    results = tool_results(fake_call)
    if fake_call.schema is BranchNotes:
        listed = _LISTED.findall("\n".join(results))
        return {
            "findings": [
                {"text": f"Lines {start} to {end} are relevant.", "evidence_ids": [eid]}
                for eid, _, start, end in list(dict.fromkeys(listed))[:8]
            ],
            "unanswered": None,
        }
    made = sum(1 for message in fake_call.messages if message["role"] == "assistant")
    if made == 0:
        return turn(call("find_definition", name=name))
    if "Where is" in sub_question:
        return done()
    if made == 1:
        key = re.search(r"^- (\S+) \(", results[0], re.M)
        assert key is not None, results[0]
        return turn(call("find_callers", symbol_key=key[1]))
    if made == 2:
        reads = [
            call("read_file", f"toolu_read_{n}", path=path, start=int(start), end=int(end))
            for n, (_, _, path, start, end) in enumerate(_CALLER.findall(results[1]))
        ]
        return turn(*reads) if reads else done()
    return done()


def write(texts: list[str]) -> AnswerDraft:
    """The writer: the block that defines the name, or the blocks whose code calls it."""
    evidence, question = texts[0], texts[-1]
    name = _NAME.findall(question)[0]
    blocks = _BLOCK.findall(evidence)
    if "Where is" in question:
        short = name.split(".")[-1]
        hits = [(short, eid) for eid, _, _, _, text in blocks if f"def {short}(" in text]
        return draft(hits[:1])
    callers: list[tuple[str, str]] = []
    for eid, _, _, _, text in blocks:
        defined = re.search(r"def (\w+)\(", text)
        if f"{name}(" in text and defined and defined[1] != name:
            callers.append((defined[1], eid))
    return draft(list(dict.fromkeys(callers)))


MODELS = {"oneshot": one_shot_model, "agent": agent_model, "multiagent": multiagent_model}


@pytest.fixture(scope="module")
def settings(pg_url: str, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return api_settings(
        database_url=pg_url,
        data_dir=tmp_path_factory.mktemp("three-way") / "data",
        refusal_min_retrieval_score=0.0,  # hash embeddings: the tuned threshold doesn't apply
    )


@pytest.fixture(scope="module")
def reports(
    pg_engine: Engine, settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, tuple[Report, Path]]]:
    """The three systems run once per module: three runs of every question each."""
    import asyncio

    root = tmp_path_factory.mktemp("three-way-reports")
    empty_store(pg_engine)
    register_suite(
        SUITE, load, {"*": score_benchmark}, prepare=prepare, enrichers=False, replace=True
    )
    written: dict[str, tuple[Report, Path]] = {}

    async def run(system: str) -> None:
        context = EvalContext(settings=settings, engine=pg_engine, llm=FakeLLM(MODELS[system]))
        options = EvalOptions(
            system=system, suite=SUITE, runs=3, max_cost_usd=5.0, cache_dir=root / "cache" / system
        )
        prepared = prepare_run(options, context=context)
        result = await run_eval(prepared.run)
        report = build_report(result, report_meta(prepared))
        path = write_report(report, root / "reports", chart=False)
        validate_report(json.loads(path.read_text(encoding="utf-8")))
        written[system] = (report, path)

    for system in SYSTEMS:
        asyncio.run(run(system))
    yield written
    empty_store(pg_engine)


def test_the_three_systems_answer_the_same_questions_through_the_runner(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    for system in SYSTEMS:
        report = reports[system][0]
        assert report.system == system and report.complete and report.runs == 3
        assert sorted(item.question_id for item in report.items) == sorted(q[0] for q in QUESTIONS)
        assert all(len(item.runs) == 3 for item in report.items)
        assert report.errored == [] and report.skipped == []
    snapshots = {tuple(s.snapshot_id for s in reports[system][0].snapshots) for system in SYSTEMS}
    assert len(snapshots) == 1  # all on the same snapshot
    multi = reports["multiagent"][0]
    assert multi.config["kind"] == "multiagent" and multi.config["limits"]["max_branches"] == 4
    # Its stand-in asks the graph in two branches, so it gets every caller set whole.
    scores = {item.question_id: item.mean_primary for item in multi.items}
    assert all(score == 1.0 for score in scores.values()), scores
    assert all(run.outcome == "answered" for item in multi.items for run in item.runs)


def test_cost_is_measured_per_question_and_per_stage(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    multi = reports["multiagent"][0]
    prices = PriceTable(DEFAULT_PRICES)
    reply = Usage(input_tokens=1_000, output_tokens=200)  # FakeLLM's, per scripted reply
    step = Usage(input_tokens=1_000, output_tokens=100)  # the scripted turns', per step
    large = prices.cost(reply, "claude-sonnet-5-5")
    notes = prices.cost(reply, "claude-haiku-5-5")
    small = prices.cost(step, "claude-haiku-5-5")
    by_question = {item.question_id: item.runs[0] for item in multi.items}
    lookup, callers = by_question["smoke-lookup-001"], by_question["smoke-callers-001"]
    # A lookup: the plan, one branch (two turns and its notes), the draft, one verification.
    assert lookup.stage_costs == pytest.approx(
        {"planner": large, "explorers": 2 * small + notes, "writer": large, "verifier": large}
    )
    # A callers question: a second branch that takes four turns.
    assert callers.stage_costs["explorers"] == pytest.approx(6 * small + 2 * notes)
    for item in multi.items:
        for run in item.runs:
            assert set(run.stage_costs) == {"planner", "explorers", "writer", "verifier"}
            assert sum(run.stage_costs.values()) == pytest.approx(run.cost_usd)
    # Per stage over the whole report: adds up to the cost per question.
    stages = stage_costs(multi)
    assert sum(stages.values()) == pytest.approx(multi.cost_per_question_usd)
    assert stages["planner"] == pytest.approx(large) and stages["verifier"] == pytest.approx(large)
    # The other systems have no stages; multi-agent is the dearest of the three.
    assert stage_costs(reports["agent"][0]) == {} and stage_costs(reports["oneshot"][0]) == {}
    costs = [reports[system][0].cost_per_question_usd for system in SYSTEMS]
    assert all(cost is not None for cost in costs)
    assert costs[0] < costs[1] < costs[2]  # type: ignore[operator]
    # An evaluation run gave the pipeline its own budget, not the one-shot answer budget.
    assert all(run.outcome != "refused_budget" for item in multi.items for run in item.runs)


def test_the_three_way_comparison_reports_scores_intervals_costs_and_stages(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    paths = [reports[system][1] for system in SYSTEMS]
    comparison = compare_many([read_report(path) for path in paths])
    assert [system.name for system in comparison.systems] == list(SYSTEMS)
    assert comparison.types == ["lookup", "callers"] and comparison.warnings == []
    assert [(pair.a, pair.b) for pair in comparison.pairs] == [
        ("oneshot", "agent"),
        ("oneshot", "multiagent"),
        ("agent", "multiagent"),
    ]
    for system in comparison.systems:
        assert system.complete and system.overall is not None and system.overall.n == 6
        for kind in ("lookup", "callers"):
            interval = system.by_type[kind]
            assert interval is not None and interval.low <= interval.mean <= interval.high
        assert system.cost is not None and set(system.cost_by_type) == {"lookup", "callers"}
    multi = comparison.systems[2]
    assert set(multi.stage_costs) == {"planner", "explorers", "writer", "verifier"}
    assert sum(multi.stage_costs.values()) == pytest.approx(multi.cost)
    assert comparison.systems[0].stage_costs == {}
    for pair in comparison.pairs:
        assert pair.overall is not None and pair.overall.n == 6
        for row in pair.by_type:
            assert row.difference is not None and row.difference.n == 3
            assert row.difference.low <= row.difference.difference <= row.difference.high
            assert row.cost_a is not None and row.cost_b is not None and row.cost_b > row.cost_a

    # The documented command prints it.
    out = paths[0].parent / "three-way.md"
    result = CliRunner().invoke(
        app, ["eval", "compare", *[str(path) for path in paths], "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    print(result.output)  # shown with pytest -s: the tables quoted in the gate evidence
    text = result.output
    lines = text.splitlines()
    assert lines[0] == f"# oneshot vs agent vs multiagent ({SUITE} dev)"
    assert "| Type | oneshot | agent | multiagent |" in text
    for heading in (
        "## Scores",
        "## Cost per question",
        "## Cost per stage: multiagent",
        "## Paired comparisons",
        f"### agent vs oneshot ({SUITE} dev)",
        f"### multiagent vs oneshot ({SUITE} dev)",
        f"### multiagent vs agent ({SUITE} dev)",
    ):
        assert heading in lines, heading
    scores = lines[lines.index("## Scores") : lines.index("## Cost per question")]
    callers = next(line for line in scores if line.startswith("| callers |"))
    assert re.fullmatch(r"\| callers \|( \d\.\d{3} \[\d\.\d{3}, \d\.\d{3}\] \|){3}", callers)
    assert callers.endswith("| 1.000 [1.000, 1.000] |")
    stage_rows = lines[lines.index("## Cost per stage: multiagent") :]
    for stage in ("planner", "explorers", "writer", "verifier"):
        shown = next(line for line in stage_rows if line.startswith(f"| {stage} |"))
        assert re.fullmatch(rf"\| {stage} \| \$\d\.\d{{4}} \| \d+% \|", shown)
    assert f"| **all** | ${multi.cost:.4f} | 100% |" in lines
    assert "Difference [95% CI]" in text and "| Cost: agent | Cost: multiagent |" in text
    assert out.read_text(encoding="utf-8") == text

    # Two reports still print the two-system table; one report is refused.
    two = CliRunner().invoke(app, ["eval", "compare", str(paths[1]), str(paths[2])])
    assert two.exit_code == 0 and two.output.splitlines()[0] == (
        f"# multiagent vs agent ({SUITE} dev)"
    )
    assert "## Scores" not in two.output
    one = CliRunner().invoke(app, ["eval", "compare", str(paths[0])])
    assert one.exit_code == 2 and "at least two reports" in one.output
    wrong = CliRunner().invoke(
        app, ["eval", "compare", *[str(path) for path in paths], "--split", "test"]
    )
    assert wrong.exit_code == 2 and "All reports must be on the test split." in wrong.output


def test_the_pipelines_branches_and_tool_calls_are_recorded_for_every_question(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    """The runner's cache holds each output with its tool calls, stage costs, and what the
    answer shows (what a scorer reads)."""
    path = reports["multiagent"][1]
    cache = path.parent.parent / "cache" / "multiagent" / "answers"
    outputs = [json.loads(p.read_text(encoding="utf-8"))["value"] for p in cache.rglob("*.json")]
    assert len(outputs) == 3 * len(QUESTIONS)
    names = [[c["name"] for c in output["tool_calls"]] for output in outputs]
    assert all("find_definition" in calls for calls in names)
    assert any("find_callers" in calls and "read_file" in calls for calls in names)
    assert all(c["allowed"] for output in outputs for c in output["tool_calls"])
    assert all(set(output["stage_costs"]) >= {"planner", "writer"} for output in outputs)
    # The sub-questions are among what the answer shows.
    assert all(any("Where is `" in text for text in output["shown"]) for output in outputs)
