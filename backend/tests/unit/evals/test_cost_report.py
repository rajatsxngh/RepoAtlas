"""``repoatlas eval cost-report`` (PRF-1, PRF-6, decision 0024): baselines, before/after pairs,
and the tolerance rule.

The rule's cases are built from a real report (the M10 test-split run of ``hybrid_graphrag``,
measured live on 2026-11-12) by changing its costs and scores: nothing here calls a model, and
the changed copies are stand-ins, not results.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.evals.costreport import (
    TOLERANCE,
    build_cost_report,
    load_reports,
    mode_change,
    mode_cost,
    render_cost_report,
    within_tolerance,
)
from repoatlas.evals.report import Report, build_report, read_report, write_report
from repoatlas.evals.runner import run_eval
from repoatlas.evals.stats import PairedDifference
from tests.unit.evals.helpers import F, ScriptedSystem, answer, ref
from tests.unit.evals.test_report import META
from tests.unit.evals.test_runner import CALLERS, LOOKUP, UNANS, make_run

REPORTS = Path(__file__).resolve().parents[4] / "eval" / "reports"
MEASURED = REPORTS / "2026-11-12-hybrid_graphrag-test.json"
BASELINES = [
    REPORTS / f"2026-11-12-{name}-test.json"
    for name in ("hybrid_graphrag", "vector_only", "bm25_only", "closed_book")
]


def measured() -> Report:
    return read_report(MEASURED)


def variant(
    report: Report,
    *,
    cost: float = 1.0,
    latency: float = 1.0,
    shift: dict[str, float] | None = None,
    system: str | None = None,
    **fields: object,
) -> Report:
    """A copy of ``report`` with every run's cost and latency scaled, and each question's score
    moved by ``shift[type]`` (kept inside 0 to 1)."""
    copy = report.model_copy(deep=True, update=fields)
    if system is not None:
        copy.system = system
    for item in copy.items:
        delta = (shift or {}).get(item.type, (shift or {}).get("*", 0.0))
        for run in item.runs:
            run.cost_usd *= cost
            run.latency_s *= latency
            if run.primary is not None:
                run.primary = min(1.0, max(0.0, run.primary + delta))
        if item.mean_primary is not None:
            item.mean_primary = min(1.0, max(0.0, item.mean_primary + delta))
    return copy


def difference(low: float) -> PairedDifference:
    return PairedDifference(
        mean_a=0.5, mean_b=0.5, difference=0.0, low=low, high=0.1, n=10, p_b_better=0.5
    )


# --- the tolerance rule ----------------------------------------------------------------------


def test_the_tolerance_is_two_points_and_the_bound_must_be_above_it() -> None:
    assert TOLERANCE == 0.02
    assert within_tolerance(difference(-0.019)) is True
    assert within_tolerance(difference(0.0)) is True
    assert within_tolerance(difference(-0.02)) is False  # above, not at
    assert within_tolerance(difference(-0.05)) is False
    assert within_tolerance(None) is None


def test_a_cheaper_setting_with_the_same_answers_is_kept() -> None:
    before = measured()
    change = mode_change(before, variant(before, cost=0.6, latency=0.8))
    assert change.verdict == "kept"
    assert change.cost_change == pytest.approx(-0.4)
    assert change.overall.cost_after == pytest.approx(0.6 * change.overall.cost_before)  # type: ignore[operator]
    assert change.overall.latency_after == pytest.approx(0.8 * change.overall.latency_before)  # type: ignore[operator]
    assert all(row.within_tolerance for row in change.by_type)
    assert change.overall.difference is not None and change.overall.difference.difference == 0.0
    assert change.overall.difference.n == 90


def test_a_drop_on_one_question_type_rejects_the_change_even_when_the_overall_holds() -> None:
    before = measured()
    change = mode_change(before, variant(before, cost=0.5, shift={"callers": -0.05}))
    by_type = {row.type: row for row in change.by_type}
    assert by_type["callers"].within_tolerance is False
    assert by_type["lookup"].within_tolerance is True
    # Five points on one of six types is under one point overall: inside the tolerance there.
    assert change.overall.within_tolerance is True
    assert change.verdict == "rejected"
    assert len(change.reasons) == 1 and change.reasons[0].startswith("callers:")


def test_a_drop_across_the_board_is_rejected_on_every_row() -> None:
    before = measured()
    change = mode_change(before, variant(before, cost=0.5, shift={"*": -0.1}))
    assert change.verdict == "rejected"
    assert change.overall.within_tolerance is False
    assert any(reason.startswith("all questions:") for reason in change.reasons)


def test_an_improvement_is_kept() -> None:
    before = measured()
    change = mode_change(before, variant(before, cost=0.9, shift={"multihop": 0.1}))
    assert change.verdict == "kept"
    multihop = next(row for row in change.by_type if row.type == "multihop")
    assert multihop.difference is not None and multihop.difference.difference > 0


def test_noise_that_could_hide_a_drop_is_rejected() -> None:
    """Scores that move up and down by the same amount leave the mean where it was, but the
    interval is wide: the rule asks for evidence that accuracy held, and there is none."""
    before = measured()
    after = before.model_copy(deep=True)
    multihop = [item for item in after.items if item.type == "multihop"]
    for index, item in enumerate(multihop):
        assert item.mean_primary is not None
        swing = 0.3 if index % 2 else -0.3
        item.mean_primary = min(1.0, max(0.0, item.mean_primary + swing))
    change = mode_change(before, after)
    row = next(row for row in change.by_type if row.type == "multihop")
    assert row.difference is not None and row.difference.low < -TOLERANCE
    assert change.verdict == "rejected"


def test_the_rule_is_not_applied_where_it_would_not_be_honest() -> None:
    before = measured()
    incomplete = mode_change(before, variant(before, cost=0.5, complete=False))
    assert incomplete.verdict == "undecided"
    assert "incomplete" in incomplete.reasons[0]

    other_version = variant(before, cost=0.5)
    other_version.suite_info = {**before.suite_info, "benchmark_version": "9.9.9"}
    changed = mode_change(before, other_version)
    assert changed.verdict == "undecided"
    assert any("benchmark_version" in reason for reason in changed.reasons)

    nothing_shared = variant(before, cost=0.5)
    for item in nothing_shared.items:
        item.question_id = f"other-{item.question_id}"
    unshared = mode_change(before, nothing_shared)
    assert unshared.verdict == "undecided"
    assert unshared.reasons[0] == "No question was scored on both sides."


# --- one report: the baseline ----------------------------------------------------------------


def test_a_baseline_reads_cost_latency_and_score_from_a_measured_report() -> None:
    report = measured()
    mode = mode_cost(report, MEASURED.name)
    runs = [run for item in report.items for run in item.runs]
    assert (mode.system, mode.split, mode.runs, mode.questions) == (
        "hybrid_graphrag",
        "test",
        3,
        90,
    )
    assert mode.cost == pytest.approx(sum(run.cost_usd for run in runs) / len(runs))
    assert mode.latency is not None and mode.latency.runs == len(runs)
    assert mode.latency.p50 <= mode.latency.p90
    assert mode.latency.mean == pytest.approx(sum(run.latency_s for run in runs) / len(runs))
    assert mode.score == report.overall
    assert set(mode.cost_by_type) == {entry.type for entry in report.by_type}
    # Reports written before tokens were kept have none, and say so.
    assert mode.tokens.cache_read_share is None


def test_the_baseline_of_the_measured_reports_renders() -> None:
    reports, skipped = load_reports(BASELINES)
    report = build_cost_report(reports)
    assert skipped == [] and report.changes == []
    assert [mode.system for mode in report.baseline] == [
        "hybrid_graphrag",
        "vector_only",
        "bm25_only",
        "closed_book",
    ]
    text = render_cost_report(report)
    assert text.startswith("# Cost baseline\n")
    assert "| 2026-11-12-hybrid_graphrag-test.json | hybrid_graphrag | test | 3 | 90 | $" in text
    assert "## closed_book on test" in text
    assert "| **all** | $" in text


def test_token_counts_give_the_share_read_from_the_cache() -> None:
    report = measured().model_copy(deep=True)
    for item in report.items:
        for run in item.runs:
            run.input_tokens, run.cache_read_tokens, run.output_tokens = 10_000, 7_500, 300
    mode = mode_cost(report)
    assert mode.tokens.input == 10_000 * 270
    assert mode.tokens.cache_read_share == pytest.approx(0.75)
    text = render_cost_report(build_cost_report([(MEASURED, report)]))
    assert "| 75% |" in text


def test_stage_costs_are_listed_before_and_after() -> None:
    before = measured().model_copy(deep=True)
    for item in before.items:
        for run in item.runs:
            run.stage_costs = {"planner": 0.004, "verifier": 0.006}
    after = variant(before, cost=0.5)
    for item in after.items:
        for run in item.runs:
            run.stage_costs = {"planner": 0.004, "verifier": 0.001}
    text = render_cost_report(build_cost_report([(MEASURED, before)], [(MEASURED, after)]))
    assert "| Stage | Before | After |" in text
    assert "| verifier | $0.0060 | $0.0010 |" in text


# --- pairing and files ------------------------------------------------------------------------


def test_reports_pair_by_system_and_split(tmp_path: Path) -> None:
    graph, vector = (read_report(path) for path in BASELINES[:2])
    report = build_cost_report(
        [(BASELINES[0], graph), (BASELINES[1], vector)],
        [(Path("after-graph.json"), variant(graph, cost=0.5))],
    )
    assert [change.system for change in report.changes] == ["hybrid_graphrag"]
    assert report.unpaired == [
        "2026-11-12-vector_only-test.json (vector_only on test) has no partner"
    ]
    with pytest.raises(ValueError, match="Two --before reports are of hybrid_graphrag on test"):
        build_cost_report(
            [(BASELINES[0], graph), (Path("again.json"), graph)],
            [(Path("after.json"), graph)],
        )


def test_a_folder_stands_for_its_reports_and_other_json_is_left_out(tmp_path: Path) -> None:
    folder = tmp_path / "before"
    folder.mkdir()
    (folder / "a.json").write_bytes(MEASURED.read_bytes())
    (folder / "calibration.json").write_text('{"rows": []}', encoding="utf-8")
    reports, skipped = load_reports([folder])
    assert [path.name for path, _ in reports] == ["a.json"]
    assert skipped == ["calibration.json is not an evaluation report and was left out."]
    # A file named outright must be a report.
    with pytest.raises(ValueError, match="is not an evaluation report"):
        load_reports([folder / "calibration.json"])


# --- through the runner, and the command ------------------------------------------------------


@pytest.mark.anyio
async def test_two_runs_of_a_stand_in_system_through_the_runner_and_the_command(
    tmp_path: Path,
) -> None:
    """The whole path the live pass will take, with a scripted system in place of the model:
    run before, run after with a cheaper setting, compare. The numbers measure the script."""
    good = {
        LOOKUP: answer([("c1", "f", [ref("src/m.py", 10, 20, F)])], cost=0.02),
        CALLERS: answer([("c1", "g", [ref("src/m.py", 22, 30)])], cost=0.02),
        UNANS: answer([], refused=True, kind="evidence", cost=0.0),
    }
    cheaper = {
        question: result.model_copy(update={"cost_usd": result.cost_usd / 2})
        for question, result in good.items()
    }
    worse = {**cheaper, LOOKUP: answer([("c1", "f", [ref("src/m.py", 40, 50)])], cost=0.01)}
    paths = {}
    for name, script in (("before", good), ("cheaper", cheaper), ("worse", worse)):
        result = await run_eval(make_run(tmp_path / name, ScriptedSystem(script), runs=3))
        paths[name] = write_report(build_report(result, META), tmp_path / name / "reports")

    out = tmp_path / "cost.md"
    kept = CliRunner().invoke(
        app,
        [
            *("eval", "cost-report", "--before", str(paths["before"])),
            *("--after", str(paths["cheaper"]), "--out", str(out)),
            *("--note", "Stand-in systems: not results."),
        ],
    )
    assert kept.exit_code == 0, kept.output
    assert "# Cost report: before and after" in kept.output
    assert "| scripted | dev | $0.0133 | $0.0067 | -50% |" in kept.output
    assert "**kept**" in kept.output
    assert "- Stand-in systems: not results." in kept.output
    assert out.read_text(encoding="utf-8") in kept.output
    stored = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert stored["tolerance"] == 0.02 and stored["changes"][0]["verdict"] == "kept"

    # Folders work too; a worse setting is rejected, and the command says so with its exit code.
    rejected = CliRunner().invoke(
        app,
        [
            *("eval", "cost-report", "--before", str(paths["before"].parent)),
            *("--after", str(paths["worse"].parent)),
        ],
    )
    assert rejected.exit_code == 4, rejected.output
    assert "## scripted on dev: rejected" in rejected.output
    assert "| lookup | 1 | 1.000 | 0.000 | -1.000 [-1.000, -1.000] | **no** |" in rejected.output

    baseline = CliRunner().invoke(app, ["eval", "cost-report", "--before", str(paths["before"])])
    assert baseline.exit_code == 0 and baseline.output.startswith("# Cost baseline")
    assert "| scripted | dev | 3 | 3 | $0.0133 |" in baseline.output

    missing = CliRunner().invoke(
        app, ["eval", "cost-report", "--before", str(tmp_path / "nowhere.json")]
    )
    assert missing.exit_code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    none = CliRunner().invoke(app, ["eval", "cost-report", "--before", str(empty)])
    assert none.exit_code == 2 and "No evaluation report was found" in none.output


@pytest.mark.anyio
async def test_reports_keep_the_tokens_of_each_run(tmp_path: Path) -> None:
    from repoatlas.llm.types import Usage

    cached = answer([("c1", "f", [ref("src/m.py", 10, 20, F)])]).model_copy(
        update={"usage": Usage(input_tokens=200, output_tokens=50, cache_read_tokens=800)}
    )
    script = {LOOKUP: cached, CALLERS: cached, UNANS: cached}
    result = await run_eval(make_run(tmp_path / "t", ScriptedSystem(script)))
    report = build_report(result, META)
    run = report.items[0].runs[0]
    assert (run.input_tokens, run.cache_read_tokens, run.output_tokens) == (1_000, 800, 50)
    assert mode_cost(report).tokens.cache_read_share == pytest.approx(0.8)
