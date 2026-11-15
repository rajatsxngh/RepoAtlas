"""``repoatlas eval compare A B`` (EVL-11): per-type differences with 95% paired intervals.

Both reports must score the same suite version and split; the comparison uses the questions
both scored (each question's mean over its runs) and reports ``B - A`` per question type and
overall, with a paired bootstrap interval (``repoatlas.evals.stats.paired_bootstrap``). A
difference whose interval excludes 0 is marked significant.

Next to each difference it shows what each system cost: dollars per question per run, per type
and overall, from the reports' own records (the system's spend, without the claim checker and
the judge). Costs are reported, not tested: a system may be better and dearer.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from repoatlas.evals.report import TYPE_ORDER, Report
from repoatlas.evals.stats import PairedDifference, paired_bootstrap


class TypeDifference(BaseModel):
    type: str
    difference: PairedDifference | None
    cost_a: float | None = None  # dollars per question per run
    cost_b: float | None = None


class Comparison(BaseModel):
    a: str
    b: str
    split: str
    suite: str
    benchmark_version: str | None
    warnings: list[str]
    by_type: list[TypeDifference]
    overall: PairedDifference | None
    cost_a: float | None = None  # dollars per question per run, all types
    cost_b: float | None = None


def cost_by_type(report: Report, ids: set[str] | None = None) -> dict[str, float]:
    """Each type's mean system cost per question per run (``"*"``: all types), over the
    questions in ``ids`` (default: every question of the report)."""
    costs: dict[str, list[float]] = defaultdict(list)
    for item in report.items:
        if ids is not None and item.question_id not in ids:
            continue
        for run in item.runs:
            costs[item.type].append(run.cost_usd)
            costs["*"].append(run.cost_usd)
    return {kind: sum(values) / len(values) for kind, values in costs.items() if values}


def question_scores(report: Report) -> dict[str, tuple[str, float]]:
    return {
        item.question_id: (item.type, item.mean_primary)
        for item in report.items
        if item.mean_primary is not None
    }


def compare_scores(
    a: dict[str, tuple[str, float]], b: dict[str, tuple[str, float]]
) -> tuple[list[TypeDifference], PairedDifference | None]:
    by_type_a: dict[str, dict[str, float]] = defaultdict(dict)
    by_type_b: dict[str, dict[str, float]] = defaultdict(dict)
    for qid, (kind, value) in a.items():
        by_type_a[kind][qid] = value
    for qid, (kind, value) in b.items():
        by_type_b[kind][qid] = value
    types = sorted(
        set(by_type_a) | set(by_type_b),
        key=lambda t: (TYPE_ORDER.index(t) if t in TYPE_ORDER else 99, t),
    )
    rows = [
        TypeDifference(type=t, difference=paired_bootstrap(by_type_a[t], by_type_b[t]))
        for t in types
    ]
    overall = paired_bootstrap(
        {qid: v for qid, (_, v) in a.items()}, {qid: v for qid, (_, v) in b.items()}
    )
    return rows, overall


def compare_reports(a: Report, b: Report, *, a_name: str = "", b_name: str = "") -> Comparison:
    warnings: list[str] = []
    if a.split != b.split:
        warnings.append(f"Different splits: {a.split} vs {b.split}.")
    if a.suite != b.suite:
        warnings.append(f"Different suites: {a.suite} vs {b.suite}.")
    for key in ("benchmark_version", "questions_sha256"):
        if a.suite_info.get(key) != b.suite_info.get(key):
            warnings.append(f"Different {key}: {a.suite_info.get(key)} vs {b.suite_info.get(key)}.")
    for report, name in ((a, "A"), (b, "B")):
        if not report.complete:
            warnings.append(f"Report {name} ({report.system}) is incomplete.")
    sa, sb = question_scores(a), question_scores(b)
    only = len(set(sa) ^ set(sb))
    if only:
        warnings.append(f"{only} questions are scored in only one report; they are left out.")
    rows, overall = compare_scores(sa, sb)
    shared = set(sa) & set(sb)
    costs_a, costs_b = cost_by_type(a, shared), cost_by_type(b, shared)
    rows = [
        row.model_copy(update={"cost_a": costs_a.get(row.type), "cost_b": costs_b.get(row.type)})
        for row in rows
    ]
    return Comparison(
        a=a_name or a.system,
        b=b_name or b.system,
        split=b.split,
        suite=b.suite,
        benchmark_version=b.suite_info.get("benchmark_version"),
        warnings=warnings,
        by_type=rows,
        overall=overall,
        cost_a=costs_a.get("*"),
        cost_b=costs_b.get("*"),
    )


def _cost(value: float | None) -> str:
    return "-" if value is None else f"${value:.4f}"


def _row(
    label: str, diff: PairedDifference | None, cost_a: float | None, cost_b: float | None
) -> str:
    costs = f"{_cost(cost_a)} | {_cost(cost_b)} |"
    if diff is None:
        return f"| {label} | 0 | - | - | - | - | {costs}"
    mark = " *" if diff.significant else ""
    return (
        f"| {label} | {diff.n} | {diff.mean_a:.3f} | {diff.mean_b:.3f} | "
        f"{diff.difference:+.3f} [{diff.low:+.3f}, {diff.high:+.3f}]{mark} | "
        f"{diff.p_b_better:.2f} | {costs}"
    )


def render_comparison(comparison: Comparison) -> str:
    lines = [
        f"# {comparison.b} vs {comparison.a} ({comparison.suite} {comparison.split})",
        "",
        f"Difference = {comparison.b} - {comparison.a}, per question (mean over runs), with 95% "
        "paired bootstrap intervals (10,000 resamples). * = the interval excludes 0. Cost is the "
        "system's own spend per question per run.",
        "",
        f"| Type | Questions | {comparison.a} | {comparison.b} | Difference [95% CI] | "
        f"P(B better) | Cost: {comparison.a} | Cost: {comparison.b} |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row(row.type, row.difference, row.cost_a, row.cost_b) for row in comparison.by_type]
    lines.append(_row("**all**", comparison.overall, comparison.cost_a, comparison.cost_b))
    if comparison.warnings:
        lines += ["", *[f"- Warning: {w}" for w in comparison.warnings]]
    return "\n".join(lines) + "\n"
