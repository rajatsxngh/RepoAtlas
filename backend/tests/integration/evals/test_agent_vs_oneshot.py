"""The agent-versus-one-shot comparison, end to end without a model (AGT-8; the M14 build gate,
decision 0022).

The real pieces run: both registered answer systems (``oneshot`` and ``agent``) answer the same
questions on a built snapshot through the eval runner, three runs each; the benchmark scorers
score them; reports are written and validated; and ``repoatlas eval compare`` prints the paired
difference per question type with 95% intervals and what each system cost per question.

Only the model is replaced, by two scripted stand-ins. **The numbers measure the stand-ins, not
the systems**: they show that the tooling works, and nothing about whether an agent beats one
retrieval pass. That is the live run, pending in ``docs/live-pass.md``.
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

from repoatlas.answers.schema import AnswerDraft, Claim
from repoatlas.bench.schema import AnswerSetTruth, Location, LookupTruth
from repoatlas.cli import app
from repoatlas.config import Settings
from repoatlas.evals.compare import compare_reports
from repoatlas.evals.registry import EvalContext, EvalItem, SuitePlan, register_suite
from repoatlas.evals.report import (
    Report,
    SnapshotUsed,
    build_report,
    read_report,
    validate_report,
    write_report,
)
from repoatlas.evals.runner import run_eval
from repoatlas.evals.scorers import score_benchmark
from repoatlas.evals.session import EvalOptions, prepare_run, report_meta
from repoatlas.llm.fake import FakeCall, FakeLLM
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import Usage
from repoatlas.pipeline.local import build_local_snapshot
from tests.integration.agent.conftest import REPOS
from tests.integration.conftest import empty_store
from tests.unit.agent.helpers import call, done, turn
from tests.unit.api.helpers import api_settings

pytestmark = pytest.mark.anyio

SUITE = "agent_smoke"
REPO = "py_small"
CART = "src/shop/cart.py"
HELPERS = "src/shop/utils/helpers.py"
ROUND = f"py:{HELPERS}:round_money:function"
FORMAT = f"py:{HELPERS}:format_price:function"
TOTAL = f"py:{CART}:Cart.total:method"
ADD = f"py:{CART}:Cart.add:method"
DESCRIBE = f"py:{CART}:Cart.describe:method"

# (id, type, question, truth): three lookups (one hop) and three caller questions (the answer
# is a set that only the graph gives whole).
QUESTIONS: list[tuple[str, str, str, Any]] = [
    (
        "smoke-lookup-001",
        "lookup",
        "Where is `round_money` defined?",
        LookupTruth(definition=Location(symbol_key=ROUND, path=HELPERS, line=4)),
    ),
    (
        "smoke-lookup-002",
        "lookup",
        "Where is `format_price` defined?",
        LookupTruth(definition=Location(symbol_key=FORMAT, path=HELPERS, line=8)),
    ),
    (
        "smoke-lookup-003",
        "lookup",
        "Where is `total` defined?",
        LookupTruth(definition=Location(symbol_key=TOTAL, path=CART, line=18)),
    ),
    (
        "smoke-callers-001",
        "callers",
        f"Which functions call `round_money` (defined in `{HELPERS}`)?",
        AnswerSetTruth(
            answers=[
                Location(symbol_key=ADD, path=CART, line=14),
                Location(symbol_key=FORMAT, path=HELPERS, line=8),
            ]
        ),
    ),
    (
        "smoke-callers-002",
        "callers",
        f"Which functions call `format_price` (defined in `{HELPERS}`)?",
        AnswerSetTruth(answers=[Location(symbol_key=DESCRIBE, path=CART, line=24)]),
    ),
    (
        "smoke-callers-003",
        "callers",
        f"Which functions call `_discount` (defined in `{CART}`)?",
        AnswerSetTruth(answers=[Location(symbol_key=DESCRIBE, path=CART, line=24)]),
    ),
]


def load(split: str) -> list[EvalItem]:
    return [
        EvalItem(id=qid, type=kind, text=text, repo_id=REPO, commit="0" * 40, split=split, truth=t)
        for qid, kind, text, t in QUESTIONS
    ]


def prepare(ctx: EvalContext, system: str, items: list[EvalItem]) -> SuitePlan:
    """The suite's own snapshot: ``py_small`` built through the pipeline (or reused)."""
    assert ctx.engine is not None
    built = build_local_snapshot(REPOS / REPO, ctx.settings, ctx.engine, name=REPO)
    used = SnapshotUsed(
        repo_id=REPO,
        snapshot_id=built.snapshot_id,
        commit=built.commit,
        config_fingerprint=built.fingerprint,
        embedding_model=ctx.settings.embedding_model,
    )
    chosen = [item.model_copy(update={"commit": built.commit}) for item in items]
    return SuitePlan(items=chosen, snapshots={REPO: used})


# ---------------------------------------------------------------------------
# Stand-ins for the model
# ---------------------------------------------------------------------------

_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")
_BLOCK = re.compile(
    r'<evidence id="(E\d+)" path="([^"]+)" lines="(\d+)-(\d+)"[^>]*>\n(.*?)\n</evidence>', re.S
)
_LISTED = re.compile(r'<evidence id="(E\d+)" path="([^"]+)" lines="(\d+)-(\d+)"[^>]*/>')
_CALLER = re.compile(r"- depth 1: (\S+) \(\w+ (\S+), (\S+) lines (\d+)-(\d+)\)")
_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.S)


def refuse(reason: str) -> AnswerDraft:
    return AnswerDraft(
        answer_markdown="The indexed code doesn't show this.",
        claims=[],
        refused=True,
        refusal_reason=reason,
    )


def draft(found: list[tuple[str, str]]) -> AnswerDraft:
    """One claim per (symbol name, evidence id)."""
    if not found:
        return refuse("Nothing in the evidence answers it.")
    claims = [
        Claim(id=f"c{n}", text=f"`{name}` is the answer.", evidence_ids=[eid], symbol=name)
        for n, (name, eid) in enumerate(found, start=1)
    ]
    marks = "".join(f"[{claim.id}]" for claim in claims)
    return AnswerDraft(answer_markdown=f"See the cited code. {marks}", claims=claims, refused=False)


def one_shot_model(fake_call: FakeCall) -> AnswerDraft:
    """Answers from the retrieved evidence only: the block that defines the name, or the blocks
    that call it."""
    content: Any = fake_call.messages[1]["content"]
    blocks = _BLOCK.findall(content[0]["text"])
    question = content[1]["text"]
    name = _NAME.findall(question)[0]
    if "Where is" in question:
        hits = [(name, eid) for eid, _, _, _, text in blocks if f"def {name}(" in text]
        return draft(hits[:1])
    callers: list[tuple[str, str]] = []
    for eid, _, _, _, text in blocks:
        defined = re.search(r"def (\w+)\(", text)
        if f"{name}(" in text and defined and defined[1] != name:
            callers.append((defined[1], eid))
    return draft(list(dict.fromkeys(callers)))


def tool_results(fake_call: FakeCall) -> list[str]:
    found: list[str] = []
    for message in fake_call.messages:
        content: Any = message["content"]
        if message["role"] == "user" and not isinstance(content, str):
            found.extend(b["content"] for b in content if b.get("type") == "tool_result")
    return found


def agent_model(fake_call: FakeCall) -> Any:
    """Looks the name up; for a callers question, asks the graph who calls it and reads each
    caller; then answers from the ledger."""
    first: Any = fake_call.messages[1]["content"]
    asked = _QUESTION.search(first[0]["text"])
    assert asked is not None
    question = asked[1]
    name = _NAME.findall(question)[0]
    lookup = "Where is" in question
    results = tool_results(fake_call)
    if fake_call.schema is not None:
        if lookup:
            listed = _LISTED.findall(results[0])
            return draft([(name.split(".")[-1], listed[0][0])] if listed else [])
        ids = {
            (path, start, end): eid
            for eid, path, start, end in _LISTED.findall("\n".join(results[2:]))
        }
        callers = [
            (qualified.split(".")[-1], ids[(path, start, end)])
            for _, qualified, path, start, end in _CALLER.findall(results[1])
            if (path, start, end) in ids
        ]
        return draft(callers)
    made = sum(1 for message in fake_call.messages if message["role"] == "assistant")
    if made == 0:
        return turn(call("find_definition", name=name))
    if lookup:
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


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings(pg_url: str, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return api_settings(
        database_url=pg_url,
        data_dir=tmp_path_factory.mktemp("agent-eval") / "data",
        refusal_min_retrieval_score=0.0,  # hash embeddings: the tuned threshold doesn't apply
    )


@pytest.fixture(scope="module")
def reports(
    pg_engine: Engine, settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, tuple[Report, Path]]]:
    """Both systems run once per module: three runs of every question each."""
    import asyncio

    root = tmp_path_factory.mktemp("agent-eval-reports")
    empty_store(pg_engine)
    register_suite(
        SUITE, load, {"*": score_benchmark}, prepare=prepare, enrichers=False, replace=True
    )
    written: dict[str, tuple[Report, Path]] = {}

    async def run(system: str, model: Any) -> None:
        context = EvalContext(settings=settings, engine=pg_engine, llm=FakeLLM(model))
        options = EvalOptions(
            system=system, suite=SUITE, runs=3, max_cost_usd=1.0, cache_dir=root / "cache" / system
        )
        prepared = prepare_run(options, context=context)
        result = await run_eval(prepared.run)
        report = build_report(result, report_meta(prepared))
        path = write_report(report, root / "reports", chart=False)
        validate_report(json.loads(path.read_text(encoding="utf-8")))
        written[system] = (report, path)

    asyncio.run(run("oneshot", one_shot_model))
    asyncio.run(run("agent", agent_model))
    yield written
    empty_store(pg_engine)


def test_both_systems_answer_the_same_questions_through_the_runner(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    oneshot, agent = reports["oneshot"][0], reports["agent"][0]
    for report in (oneshot, agent):
        assert report.complete and report.suite == SUITE and report.runs == 3
        assert sorted(item.question_id for item in report.items) == sorted(q[0] for q in QUESTIONS)
        assert all(len(item.runs) == 3 for item in report.items)
        assert report.errored == []
    assert (oneshot.system, agent.system) == ("oneshot", "agent")
    assert oneshot.config["kind"] == "retrieval" and agent.config["kind"] == "agent"
    assert agent.config["max_steps"] == 12
    # Both ran on the same snapshot.
    assert [s.snapshot_id for s in oneshot.snapshots] == [s.snapshot_id for s in agent.snapshots]
    # The agent stand-in asks the graph, so it gets every caller set whole.
    scores = {item.question_id: item.mean_primary for item in agent.items}
    assert all(score == 1.0 for score in scores.values()), scores


def test_cost_per_question_is_measured_for_both_systems(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    oneshot, agent = reports["oneshot"][0], reports["agent"][0]
    prices = PriceTable(DEFAULT_PRICES)
    usage = Usage(input_tokens=1_000, output_tokens=200)  # FakeLLM's, per final call
    step = Usage(input_tokens=1_000, output_tokens=100)  # the scripted turns', per step
    large = prices.cost(usage, "claude-sonnet-5-5")
    small = prices.cost(step, "claude-haiku-5-5")
    assert oneshot.cost_per_question_usd == pytest.approx(large)
    costs = {item.question_id: item.runs[0].cost_usd for item in agent.items}
    # A lookup is two exploration turns and the final call; a callers question is four.
    assert costs["smoke-lookup-001"] == pytest.approx(2 * small + large)
    assert costs["smoke-callers-001"] == pytest.approx(4 * small + large)
    assert agent.cost_per_question_usd == pytest.approx(3 * small + large)
    assert agent.cost_per_question_usd is not None and oneshot.cost_per_question_usd is not None
    assert agent.cost_per_question_usd > oneshot.cost_per_question_usd


def test_the_comparison_reports_differences_with_intervals_and_cost_per_type(
    reports: dict[str, tuple[Report, Path]],
) -> None:
    (oneshot, a_path), (agent, b_path) = reports["oneshot"], reports["agent"]
    comparison = compare_reports(read_report(a_path), read_report(b_path))
    assert (comparison.a, comparison.b, comparison.suite) == ("oneshot", "agent", SUITE)
    assert comparison.warnings == []
    assert [row.type for row in comparison.by_type] == ["lookup", "callers"]
    for row in comparison.by_type:
        difference = row.difference
        assert difference is not None and difference.n == 3
        assert difference.low <= difference.difference <= difference.high  # a 95% interval
        assert difference.mean_b == 1.0
        assert row.cost_a is not None and row.cost_b is not None and row.cost_b > row.cost_a
    assert comparison.overall is not None and comparison.overall.n == 6
    assert comparison.cost_a == pytest.approx(oneshot.cost_per_question_usd)
    assert comparison.cost_b == pytest.approx(agent.cost_per_question_usd)

    # The documented command prints the same table.
    result = CliRunner().invoke(app, ["eval", "compare", str(a_path), str(b_path)])
    assert result.exit_code == 0, result.output
    print(result.output)  # shown with pytest -s: the table quoted in the gate evidence
    lines = result.output.splitlines()
    assert lines[0] == f"# agent vs oneshot ({SUITE} dev)"
    assert "| Type | Questions | oneshot | agent | Difference [95% CI] |" in result.output
    assert "| Cost: oneshot | Cost: agent |" in result.output
    rows = {line.split(" | ")[0].strip("| *"): line for line in lines if line.startswith("| ")}
    assert set(rows) >= {"lookup", "callers", "all"}
    assert re.search(r"\| callers \| 3 \| \d\.\d{3} \| 1\.000 \| [+-]\d\.\d{3} \[", rows["callers"])
    assert rows["callers"].endswith(f"| ${comparison.by_type[1].cost_b:.4f} |")


def test_the_agents_tool_calls_are_recorded_for_every_question(
    reports: dict[str, tuple[Report, Path]], pg_engine: Engine, settings: Settings
) -> None:
    """The runner's cache holds each agent output with its tool calls (what a scorer reads)."""
    path = reports["agent"][1]
    cache = path.parent.parent / "cache" / "agent" / "answers"
    outputs = [json.loads(p.read_text(encoding="utf-8"))["value"] for p in cache.rglob("*.json")]
    assert len(outputs) == 3 * len(QUESTIONS)
    names = [[c["name"] for c in output["tool_calls"]] for output in outputs]
    assert all(calls[0] == "find_definition" for calls in names)
    assert any("find_callers" in calls and "read_file" in calls for calls in names)
    assert all(c["allowed"] for output in outputs for c in output["tool_calls"])
