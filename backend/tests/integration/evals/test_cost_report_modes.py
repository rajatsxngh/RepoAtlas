"""The before/after cost report, end to end without a model (PRF-6; the M16 build gate,
decision 0024).

The three answering modes (``oneshot``, ``agent``, ``multiagent``) answer the same questions on
a built snapshot through the eval runner, three runs each, twice: once as "before", and once
as "after" with a stand-in whose calls read most of their input from the prompt cache, so it
answers the same and costs less. ``repoatlas eval cost-report`` pairs the two sets of reports
by mode, prints cost and latency per question before and after, the paired accuracy difference
with its 95% interval, and the verdict under the tolerance rule. A third set, in which the
one-shot stand-in stops finding callers, must be rejected for that mode only.

Only the model is replaced, by scripted stand-ins. **The numbers measure the stand-ins, not the
systems**: they show that the report works, and nothing about what prompt caching or a cheaper
setting saves. That is the live run, pending in ``docs/live-pass.md`` (items 20 to 23).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.config import Settings
from repoatlas.evals.costreport import build_cost_report, load_reports
from repoatlas.evals.registry import EvalContext, register_suite
from repoatlas.evals.report import build_report, validate_report, write_report
from repoatlas.evals.runner import run_eval
from repoatlas.evals.scorers import score_benchmark
from repoatlas.evals.session import EvalOptions, prepare_run, report_meta
from repoatlas.llm.fake import FakeCall, FakeLLM
from repoatlas.llm.types import LLMResult, Usage
from tests.integration.conftest import empty_store
from tests.integration.evals.test_agent_vs_oneshot import draft, load, prepare
from tests.integration.evals.test_three_way import MODELS, SYSTEMS
from tests.unit.api.helpers import api_settings

SUITE = "cost_report_smoke"
# What a call reports once most of its input is read from the cache.
CACHED = Usage(input_tokens=250, cache_read_tokens=750, output_tokens=200)


def cached(model: Callable[[FakeCall], Any]) -> Callable[[FakeCall], Any]:
    """The same stand-in, with every scripted turn's input mostly read from the cache."""

    def answer(fake_call: FakeCall) -> Any:
        result = model(fake_call)
        if isinstance(result, LLMResult):
            usage = Usage(
                input_tokens=result.usage.input_tokens // 4,
                cache_read_tokens=result.usage.input_tokens - result.usage.input_tokens // 4,
                output_tokens=result.usage.output_tokens,
            )
            return result.model_copy(update={"usage": usage})
        return result

    return answer


def forgetful(fake_call: FakeCall) -> Any:
    """A one-shot stand-in that finds definitions but no callers."""
    question = fake_call.messages[1]["content"][1]["text"]
    if "Where is" in question:
        return MODELS["oneshot"](fake_call)
    return draft([])


@pytest.fixture(scope="module")
def settings(pg_url: str, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return api_settings(
        database_url=pg_url,
        data_dir=tmp_path_factory.mktemp("cost-report") / "data",
        refusal_min_retrieval_score=0.0,  # hash embeddings: the tuned threshold doesn't apply
    )


@pytest.fixture(scope="module")
def folders(
    pg_engine: Engine, settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, Path]]:
    """Three folders of reports: before, after (cheaper, same answers), and worse."""
    root = tmp_path_factory.mktemp("cost-report-runs")
    empty_store(pg_engine)
    register_suite(
        SUITE, load, {"*": score_benchmark}, prepare=prepare, enrichers=False, replace=True
    )

    async def run(side: str, system: str, llm: FakeLLM) -> None:
        context = EvalContext(settings=settings, engine=pg_engine, llm=llm)
        options = EvalOptions(
            system=system,
            suite=SUITE,
            runs=3,
            max_cost_usd=5.0,
            cache_dir=root / "cache" / side / system,
        )
        prepared = prepare_run(options, context=context)
        report = build_report(await run_eval(prepared.run), report_meta(prepared))
        path = write_report(report, root / side, chart=False)
        validate_report(json.loads(path.read_text(encoding="utf-8")))

    for system in SYSTEMS:
        asyncio.run(run("before", system, FakeLLM(MODELS[system])))
        asyncio.run(run("after", system, FakeLLM(cached(MODELS[system]), usage=CACHED)))
    asyncio.run(run("worse", "oneshot", FakeLLM(forgetful, usage=CACHED)))
    yield {side: root / side for side in ("before", "after", "worse")}
    empty_store(pg_engine)


def test_each_mode_is_paired_and_a_cheaper_run_with_the_same_answers_is_kept(
    folders: dict[str, Path],
) -> None:
    before, _ = load_reports([folders["before"]])
    after, _ = load_reports([folders["after"]])
    report = build_cost_report(before, after)
    assert sorted(change.system for change in report.changes) == sorted(SYSTEMS)
    assert report.unpaired == []
    for change in report.changes:
        assert change.verdict == "kept", (change.system, change.reasons)
        assert change.warnings == []
        assert change.overall.difference is not None
        assert change.overall.difference.n == 6  # the six questions, paired
        assert change.overall.difference.difference == 0.0
        assert {row.type for row in change.by_type} == {"lookup", "callers"}
        assert all(row.within_tolerance for row in change.by_type)
        # The same calls, cheaper: every mode's cost per question fell.
        assert change.cost_change is not None and change.cost_change < -0.3
        assert change.before.tokens.cache_read_share == 0.0
        assert change.after.tokens.cache_read_share == pytest.approx(0.75, abs=0.01)
        assert change.before.runs == change.after.runs == 3
    by_system = {change.system: change for change in report.changes}
    # Stages only where a mode has them, on both sides.
    stages = {"planner", "explorers", "writer", "verifier"}
    assert set(by_system["multiagent"].before.stage_costs) == stages
    assert set(by_system["multiagent"].after.stage_costs) == stages
    assert by_system["agent"].before.stage_costs == {}
    costs = [by_system[system].overall.cost_before for system in SYSTEMS]
    assert costs[0] < costs[1] < costs[2]  # type: ignore[operator]


def test_the_command_prints_the_report_for_every_mode(folders: dict[str, Path]) -> None:
    out = folders["after"].parent / "cost-report.md"
    result = CliRunner().invoke(
        app,
        [
            *("eval", "cost-report", "--before", str(folders["before"])),
            *("--after", str(folders["after"]), "--out", str(out)),
            *("--note", "Stand-in systems: these numbers measure scripts, not the product."),
        ],
    )
    assert result.exit_code == 0, result.output
    print(result.output)  # shown with pytest -s: the tables quoted in the gate evidence
    text = result.output
    assert text.startswith("# Cost report: before and after\n")
    assert "above -0.02" in text
    for system in SYSTEMS:
        assert f"## {system} on dev: kept" in text
    summary = [line for line in text.splitlines() if line.endswith("| **kept** |")]
    assert len(summary) == 3
    assert "Input tokens read from the prompt cache: 0% before, 75% after." in text
    assert "| Stage | Before | After |" in text and "| verifier | $" in text
    assert "| callers | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes |" in text
    assert "- Stand-in systems: these numbers measure scripts, not the product." in text
    stored = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert [change["verdict"] for change in stored["changes"]] == ["kept"] * 3


def test_a_mode_that_lost_accuracy_is_rejected_and_the_others_are_left_unpaired(
    folders: dict[str, Path],
) -> None:
    result = CliRunner().invoke(
        app,
        [
            *("eval", "cost-report", "--before", str(folders["before"])),
            *("--after", str(folders["worse"])),
        ],
    )
    assert result.exit_code == 4, result.output
    text = result.output
    assert "## oneshot on dev: rejected" in text
    assert "- callers: the interval's lower bound is" in text
    assert "| lookup | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes |" in text
    assert "(agent on dev) has no partner" in text and "(multiagent on dev) has no partner" in text

    # The baseline alone: one row per mode, with cost, latency, and score.
    baseline = CliRunner().invoke(app, ["eval", "cost-report", "--before", str(folders["before"])])
    assert baseline.exit_code == 0 and baseline.output.startswith("# Cost baseline\n")
    for system in SYSTEMS:
        assert f"| {system} | dev | 3 | 6 | $" in baseline.output
