from __future__ import annotations

import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.evals.compare import compare_reports, compare_scores, render_comparison
from repoatlas.evals.report import build_report, write_report
from repoatlas.evals.runner import run_eval
from repoatlas.evals.stats import bootstrap_ci, paired_bootstrap, question_means
from tests.unit.evals.helpers import F, ScriptedSystem, answer, ref
from tests.unit.evals.test_report import META
from tests.unit.evals.test_runner import CALLERS, LOOKUP, UNANS, make_run


def test_question_means_average_runs_and_skip_missing() -> None:
    means = question_means([("q1", 1.0), ("q1", 0.0), ("q2", None), ("q2", 0.5), ("q3", None)])
    assert means == {"q1": 0.5, "q2": 0.5}


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic() -> None:
    rng = random.Random(1)
    values = [rng.random() for _ in range(60)]
    ci = bootstrap_ci(values, resamples=2_000)
    assert ci is not None and ci.low < ci.mean < ci.high and ci.n == 60
    assert bootstrap_ci(values, resamples=2_000) == ci
    assert bootstrap_ci([]) is None
    single = bootstrap_ci([0.4])
    assert single is not None and (single.low, single.high) == (0.4, 0.4)
    constant = bootstrap_ci([1.0] * 10, resamples=500)
    assert constant is not None and (constant.low, constant.high) == (1.0, 1.0)


def test_a_known_difference_is_detected_and_no_difference_is_not() -> None:
    rng = random.Random(7)
    a = {f"q{i}": rng.random() * 0.5 for i in range(80)}
    better = {qid: value + 0.2 + rng.uniform(-0.05, 0.05) for qid, value in a.items()}
    diff = paired_bootstrap(a, better, resamples=2_000)
    assert diff is not None
    assert diff.difference == pytest.approx(0.2, abs=0.02)
    assert diff.low > 0.15 and diff.high < 0.25 and diff.significant
    assert diff.p_b_better == 1.0
    noise = {qid: value + rng.uniform(-0.1, 0.1) for qid, value in a.items()}
    same = paired_bootstrap(a, noise, resamples=2_000)
    assert same is not None and same.low < 0 < same.high and not same.significant
    worse = paired_bootstrap(better, a, resamples=2_000)
    assert worse is not None and worse.high < 0


def test_pairing_uses_only_shared_questions() -> None:
    diff = paired_bootstrap({"q1": 0.0, "q2": 1.0}, {"q2": 1.0, "q3": 0.0}, resamples=100)
    assert diff is not None and diff.n == 1 and diff.difference == 0.0
    assert paired_bootstrap({"q1": 1.0}, {"q2": 1.0}) is None


def test_compare_scores_per_type() -> None:
    a = {"l1": ("lookup", 0.0), "l2": ("lookup", 0.0), "c1": ("callers", 0.5)}
    b = {"l1": ("lookup", 1.0), "l2": ("lookup", 1.0), "c1": ("callers", 0.5)}
    rows, overall = compare_scores(a, b)
    assert [row.type for row in rows] == ["lookup", "callers"]
    assert rows[0].difference is not None and rows[0].difference.difference == 1.0
    assert rows[1].difference is not None and rows[1].difference.difference == 0.0
    assert overall is not None and overall.difference == pytest.approx(2 / 3)


@pytest.mark.anyio
async def test_compare_reports_and_the_cli(tmp_path: Path) -> None:
    good = {
        LOOKUP: answer([("c1", "f", [ref("src/m.py", 10, 20, F)])]),
        CALLERS: answer([("c1", "g", [ref("src/m.py", 22, 30)])]),
        UNANS: answer([], refused=True, kind="evidence"),
    }
    bad = {**good, LOOKUP: answer([("c1", "f", [ref("src/m.py", 40, 50)])])}
    ra = build_report(await run_eval(make_run(tmp_path / "a", ScriptedSystem(bad))), META)
    rb = build_report(await run_eval(make_run(tmp_path / "b", ScriptedSystem(good))), META)
    comparison = compare_reports(ra, rb, a_name="bad", b_name="good")
    lookup = next(row for row in comparison.by_type if row.type == "lookup")
    assert lookup.difference is not None and lookup.difference.difference == 1.0
    assert comparison.warnings == []
    text = render_comparison(comparison)
    assert "| lookup | 1 | 0.000 | 1.000 | +1.000" in text

    pa = write_report(ra, tmp_path / "reports")
    pb = write_report(rb, tmp_path / "reports")
    result = CliRunner().invoke(
        app, ["eval", "compare", str(pa), str(pb), "--out", str(tmp_path / "c.md")]
    )
    assert result.exit_code == 0, result.output
    assert "Difference [95% CI]" in result.output
    assert (tmp_path / "c.md").is_file()
    refused = CliRunner().invoke(app, ["eval", "compare", str(pa), str(pb), "--split", "test"])
    assert refused.exit_code == 2
