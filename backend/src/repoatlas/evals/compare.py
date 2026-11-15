"""``repoatlas eval compare A B`` (EVL-11): per-type differences with 95% paired intervals.

Both reports must score the same suite version and split; the comparison uses the questions
both scored (each question's mean over its runs) and reports ``B - A`` per question type and
overall, with a paired bootstrap interval (``repoatlas.evals.stats.paired_bootstrap``). A
difference whose interval excludes 0 is marked significant.

Next to each difference it shows what each system cost: dollars per question per run, per type
and overall, from the reports' own records (the system's spend, without the claim checker and
the judge). Costs are reported, not tested: a system may be better and dearer.

``repoatlas eval compare A B C …`` (MAS-7) compares more than two systems: a summary with every
system's score per question type and its 95% interval, each system's cost per question, the
cost per stage of systems that report stages (a multi-agent answer's planner, explorers,
writer, and verifier), and then the paired comparison of every pair, later minus earlier.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from repoatlas.evals.report import TYPE_ORDER, Report
from repoatlas.evals.stats import Interval, PairedDifference, paired_bootstrap


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


# ---------------------------------------------------------------------------
# More than two systems
# ---------------------------------------------------------------------------


class SystemSummary(BaseModel):
    """One system's own numbers, from its report."""

    name: str
    overall: Interval | None
    by_type: dict[str, Interval | None]
    cost: float | None  # dollars per question per run, all types
    cost_by_type: dict[str, float]
    stage_costs: dict[str, float]  # dollars per question per run, by stage (may be empty)
    complete: bool


class MultiComparison(BaseModel):
    split: str
    suite: str
    systems: list[SystemSummary]
    types: list[str]
    pairs: list[Comparison]  # every pair, later minus earlier, in the order given
    warnings: list[str]


def stage_costs(report: Report) -> dict[str, float]:
    """Each stage's mean cost per question per run, over every run of the report, for a system
    that reports stages. A run that never reached a stage counts as zero for it, so the stages
    add up to the system's cost per question."""
    runs = [run for item in report.items for run in item.runs]
    if not runs or not any(run.stage_costs for run in runs):
        return {}
    totals: dict[str, float] = defaultdict(float)
    for run in runs:
        for stage, cost in run.stage_costs.items():
            totals[stage] += cost
    return {stage: total / len(runs) for stage, total in totals.items()}


def unique_names(reports: list[Report]) -> list[str]:
    """The systems' names; a name used twice gets its position added."""
    names = [report.system for report in reports]
    return [
        name if names.count(name) == 1 else f"{name}#{index}"
        for index, name in enumerate(names, start=1)
    ]


def compare_many(reports: list[Report]) -> MultiComparison:
    """Every system's summary and every pair's paired comparison. Needs at least two."""
    if len(reports) < 2:
        raise ValueError("A comparison needs at least two reports.")
    names = unique_names(reports)
    systems = []
    for report, name in zip(reports, names, strict=True):
        costs = cost_by_type(report)
        systems.append(
            SystemSummary(
                name=name,
                overall=report.overall,
                by_type={entry.type: entry.primary for entry in report.by_type},
                cost=costs.get("*"),
                cost_by_type={kind: cost for kind, cost in costs.items() if kind != "*"},
                stage_costs=stage_costs(report),
                complete=report.complete,
            )
        )
    pairs = [
        compare_reports(reports[i], reports[j], a_name=names[i], b_name=names[j])
        for i in range(len(reports))
        for j in range(i + 1, len(reports))
    ]
    types = sorted(
        {kind for system in systems for kind in system.by_type},
        key=lambda t: (TYPE_ORDER.index(t) if t in TYPE_ORDER else 99, t),
    )
    warnings = list(dict.fromkeys(warning for pair in pairs for warning in pair.warnings))
    return MultiComparison(
        split=reports[-1].split,
        suite=reports[-1].suite,
        systems=systems,
        types=types,
        pairs=pairs,
        warnings=warnings,
    )


def _score(interval: Interval | None) -> str:
    if interval is None:
        return "-"
    return f"{interval.mean:.3f} [{interval.low:.3f}, {interval.high:.3f}]"


STAGE_ORDER = ("planner", "explorers", "writer", "verifier")


def render_many(comparison: MultiComparison) -> str:
    systems = comparison.systems
    names = [system.name for system in systems]
    header = " | ".join(names)
    rule = "|".join("---" for _ in range(len(names) + 1))
    lines = [
        f"# {' vs '.join(names)} ({comparison.suite} {comparison.split})",
        "",
        "## Scores",
        "",
        "Each system's mean over its questions (each question averaged over its runs), with "
        "its own 95% bootstrap interval. Intervals of different systems are not a test of "
        "their difference; the paired comparisons below are.",
        "",
        f"| Type | {header} |",
        f"|{rule}|",
    ]
    for kind in comparison.types:
        cells = " | ".join(_score(system.by_type.get(kind)) for system in systems)
        lines.append(f"| {kind} | {cells} |")
    lines.append(f"| **all** | {' | '.join(_score(system.overall) for system in systems)} |")
    lines += [
        "",
        "## Cost per question",
        "",
        "The system's own spend per question per run (without the claim checker and the judge).",
        "",
        f"| Type | {header} |",
        f"|{rule}|",
    ]
    for kind in comparison.types:
        cells = " | ".join(_cost(system.cost_by_type.get(kind)) for system in systems)
        lines.append(f"| {kind} | {cells} |")
    lines.append(f"| **all** | {' | '.join(_cost(system.cost) for system in systems)} |")
    staged = [system for system in systems if system.stage_costs]
    for system in staged:
        stages = sorted(
            system.stage_costs,
            key=lambda stage: (STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 99, stage),
        )
        total = sum(system.stage_costs.values())
        lines += [
            "",
            f"## Cost per stage: {system.name}",
            "",
            "Per question per run; a run that stopped before a stage counts as zero for it.",
            "",
            "| Stage | Cost | Share |",
            "|---|---|---|",
        ]
        for stage in stages:
            cost = system.stage_costs[stage]
            share = f"{100 * cost / total:.0f}%" if total else "-"
            lines.append(f"| {stage} | {_cost(cost)} | {share} |")
        lines.append(f"| **all** | {_cost(total)} | 100% |")
    lines += ["", "## Paired comparisons", ""]
    for pair in comparison.pairs:
        body = render_comparison(pair.model_copy(update={"warnings": []})).splitlines()
        lines += [f"### {body[0].removeprefix('# ')}", *body[1:], ""]
    if comparison.warnings:
        lines += [*[f"- Warning: {w}" for w in comparison.warnings], ""]
    return "\n".join(lines).rstrip("\n") + "\n"
