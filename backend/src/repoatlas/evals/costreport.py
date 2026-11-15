"""``repoatlas eval cost-report`` (PRF-1, PRF-6, decision 0024): what answering costs per mode,
before and after a change, and whether accuracy stayed inside the tolerance.

With only ``--before`` reports it prints the baseline: each report's cost and latency per
question (per question type and overall), its score with a 95% interval, its cost per stage
where the system reports stages, and the share of input tokens read from the prompt cache
where the report records tokens.

With ``--after`` reports it pairs the two sides by system and split and adds, per pair, the
paired accuracy difference (after minus before, ``repoatlas.evals.stats.paired_bootstrap``)
per question type and overall, and a verdict under the tolerance rule:

    A cheaper setting is kept only if, for every question type and overall, the lower bound
    of the 95% interval of the paired difference in the primary metric is above -0.02.

``kept`` and ``rejected`` are that rule's outcomes. A pair is ``undecided`` when the rule
can't be applied honestly: no question scored on both sides, a report that stopped at its
cost cap, or reports of different benchmark versions.

The tool reads reports; it calls no model. Its numbers are only as real as the reports it is
given: reports of stand-in systems measure scripts.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Literal

from jsonschema import ValidationError as SchemaError  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

from repoatlas.evals.compare import compare_reports, cost_by_type, stage_costs
from repoatlas.evals.report import TYPE_ORDER, Report, read_report
from repoatlas.evals.stats import Interval, PairedDifference
from repoatlas.llm.pricing import ModelPrice

TOLERANCE = 0.02  # two points on the primary metric, which runs from 0 to 1
ALL = "*"
Verdict = Literal["kept", "rejected", "undecided"]


class Latency(BaseModel):
    """Seconds per question per run, over the runs that were not served from the cache."""

    mean: float
    p50: float
    p90: float
    runs: int


class Tokens(BaseModel):
    """Token totals of a report's runs (zero for reports written before tokens were kept)."""

    input: int = 0  # every input token: uncached, cache reads, and cache writes
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def cache_read_share(self) -> float | None:
        """The share of input tokens read from the cache; ``None`` when none were recorded."""
        return self.cache_read / self.input if self.input else None


class ModeCost(BaseModel):
    """One report's cost, latency, and score."""

    system: str
    split: str
    suite: str
    source: str  # the report's file name
    created_at: str
    models: dict[str, str]
    runs: int
    questions: int
    complete: bool
    score: Interval | None
    score_by_type: dict[str, Interval | None]
    cost: float | None  # dollars per question per run, the system's own spend
    cost_by_type: dict[str, float]
    stage_costs: dict[str, float]
    latency: Latency | None
    latency_by_type: dict[str, float]  # mean seconds
    tokens: Tokens
    total_cost_usd: float


class TypeChange(BaseModel):
    type: str
    difference: PairedDifference | None
    cost_before: float | None
    cost_after: float | None
    latency_before: float | None
    latency_after: float | None
    within_tolerance: bool | None  # None: nothing to compare


class ModeChange(BaseModel):
    system: str
    split: str
    before: ModeCost
    after: ModeCost
    by_type: list[TypeChange]
    overall: TypeChange
    verdict: Verdict
    reasons: list[str]
    warnings: list[str]

    @property
    def cost_change(self) -> float | None:
        """The relative change in cost per question (negative: cheaper)."""
        before, after = self.overall.cost_before, self.overall.cost_after
        if before is None or after is None or before == 0:
            return None
        return (after - before) / before


class CostReport(BaseModel):
    tolerance: float
    baseline: list[ModeCost]  # the --before reports, when there is no --after
    changes: list[ModeChange]
    unpaired: list[str]  # reports with no partner on the other side
    notes: list[str]


# --- one report -------------------------------------------------------------------------------


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _latency(
    report: Report, ids: set[str] | None = None
) -> tuple[Latency | None, dict[str, float]]:
    """Latency over the runs that actually ran (a run answered from the result cache has the
    latency of its first run, which is still the system's; errored runs are left out)."""
    by_type: dict[str, list[float]] = defaultdict(list)
    for item in report.items:
        if ids is not None and item.question_id not in ids:
            continue
        for run in item.runs:
            if run.error is None and run.latency_s > 0:
                by_type[item.type].append(run.latency_s)
    every = [value for values in by_type.values() for value in values]
    if not every:
        return None, {}
    overall = Latency(
        mean=statistics.fmean(every),
        p50=_percentile(every, 0.5),
        p90=_percentile(every, 0.9),
        runs=len(every),
    )
    return overall, {kind: statistics.fmean(values) for kind, values in by_type.items()}


def _tokens(report: Report) -> Tokens:
    runs = [run for item in report.items for run in item.runs]
    return Tokens(
        input=sum(run.input_tokens for run in runs),
        output=sum(run.output_tokens for run in runs),
        cache_read=sum(run.cache_read_tokens for run in runs),
        cache_write=sum(run.cache_write_tokens for run in runs),
    )


def mode_cost(report: Report, source: str = "") -> ModeCost:
    costs = cost_by_type(report)
    latency, latency_by_type = _latency(report)
    return ModeCost(
        system=report.system,
        split=report.split,
        suite=report.suite,
        source=source,
        created_at=report.created_at,
        models=report.models,
        runs=report.runs,
        questions=len(report.items),
        complete=report.complete,
        score=report.overall,
        score_by_type={entry.type: entry.primary for entry in report.by_type},
        cost=costs.get(ALL),
        cost_by_type={kind: cost for kind, cost in costs.items() if kind != ALL},
        stage_costs=stage_costs(report),
        latency=latency,
        latency_by_type=latency_by_type,
        tokens=_tokens(report),
        total_cost_usd=report.total_cost_usd,
    )


# --- before and after -------------------------------------------------------------------------


def within_tolerance(
    difference: PairedDifference | None, tolerance: float = TOLERANCE
) -> bool | None:
    """The tolerance rule for one row: the interval's lower bound is above ``-tolerance``."""
    if difference is None:
        return None
    return difference.low > -tolerance


def mode_change(
    before: Report,
    after: Report,
    *,
    before_source: str = "",
    after_source: str = "",
    tolerance: float = TOLERANCE,
) -> ModeChange:
    comparison = compare_reports(before, after)
    shared = {item.question_id for item in before.items if item.mean_primary is not None} & {
        item.question_id for item in after.items if item.mean_primary is not None
    }
    _, latency_before = _latency(before, shared)
    _, latency_after = _latency(after, shared)
    overall_before, _ = _latency(before, shared)
    overall_after, _ = _latency(after, shared)
    rows = [
        TypeChange(
            type=row.type,
            difference=row.difference,
            cost_before=row.cost_a,
            cost_after=row.cost_b,
            latency_before=latency_before.get(row.type),
            latency_after=latency_after.get(row.type),
            within_tolerance=within_tolerance(row.difference, tolerance),
        )
        for row in comparison.by_type
    ]
    overall = TypeChange(
        type=ALL,
        difference=comparison.overall,
        cost_before=comparison.cost_a,
        cost_after=comparison.cost_b,
        latency_before=overall_before.mean if overall_before else None,
        latency_after=overall_after.mean if overall_after else None,
        within_tolerance=within_tolerance(comparison.overall, tolerance),
    )
    verdict, reasons = _verdict(before, after, rows, overall, tolerance)
    return ModeChange(
        system=after.system,
        split=after.split,
        before=mode_cost(before, before_source),
        after=mode_cost(after, after_source),
        by_type=rows,
        overall=overall,
        verdict=verdict,
        reasons=reasons,
        warnings=comparison.warnings,
    )


def _verdict(
    before: Report,
    after: Report,
    rows: list[TypeChange],
    overall: TypeChange,
    tolerance: float,
) -> tuple[Verdict, list[str]]:
    undecided: list[str] = []
    if overall.difference is None:
        undecided.append("No question was scored on both sides.")
    for name, report in (("before", before), ("after", after)):
        if not report.complete:
            undecided.append(f"The {name} report is incomplete (it stopped at its cost cap).")
    for key in ("benchmark_version", "questions_sha256"):
        if before.suite_info.get(key) != after.suite_info.get(key):
            undecided.append(f"The two reports have a different {key}.")
    if before.suite != after.suite:
        undecided.append(
            f"The two reports are of different suites ({before.suite}, {after.suite})."
        )
    if undecided:
        return "undecided", undecided
    outside = [row for row in [*rows, overall] if row.within_tolerance is False]
    if outside:
        reasons = []
        for row in outside:
            assert row.difference is not None
            label = "all questions" if row.type == ALL else row.type
            reasons.append(
                f"{label}: the interval's lower bound is {row.difference.low:+.3f}, "
                f"not above {-tolerance:+.3f}."
            )
        return "rejected", reasons
    return "kept", [
        f"Every question type and the overall difference have a lower bound above "
        f"{-tolerance:+.3f}."
    ]


def expand(paths: list[Path]) -> list[Path]:
    """Report files named by ``paths``: a directory stands for the ``*.json`` files in it."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.glob("*.json")))
        else:
            found.append(path)
    return found


def load_reports(paths: list[Path]) -> tuple[list[tuple[Path, Report]], list[str]]:
    """The evaluation reports among ``paths`` (directories expanded), and a note for every
    JSON file of a directory that is not one (a calibration or an embedding comparison)."""
    reports: list[tuple[Path, Report]] = []
    skipped: list[str] = []
    named = {path for path in paths if not path.is_dir()}
    for path in expand(paths):
        try:
            reports.append((path, read_report(path)))
        except (ValidationError, SchemaError, ValueError) as error:
            if path in named:
                raise ValueError(f"{path} is not an evaluation report.") from error
            skipped.append(f"{path.name} is not an evaluation report and was left out.")
    return reports, skipped


def build_cost_report(
    before: list[tuple[Path, Report]],
    after: list[tuple[Path, Report]] | None = None,
    *,
    tolerance: float = TOLERANCE,
    notes: list[str] | None = None,
) -> CostReport:
    """The baseline of ``before``, or, with ``after``, each (system, split) pair's change.
    Two reports of one system and split on the same side are ambiguous and refused."""
    out_notes = list(notes or [])
    if not after:
        return CostReport(
            tolerance=tolerance,
            baseline=[mode_cost(report, path.name) for path, report in before],
            changes=[],
            unpaired=[],
            notes=out_notes,
        )
    sides: list[dict[tuple[str, str], tuple[Path, Report]]] = []
    for name, side in (("--before", before), ("--after", after)):
        keyed: dict[tuple[str, str], tuple[Path, Report]] = {}
        for path, report in side:
            key = (report.system, report.split)
            if key in keyed:
                raise ValueError(
                    f"Two {name} reports are of {report.system} on {report.split} "
                    f"({keyed[key][0].name}, {path.name}); give one per system and split."
                )
            keyed[key] = (path, report)
        sides.append(keyed)
    befores, afters = sides
    changes = [
        mode_change(
            befores[key][1],
            afters[key][1],
            before_source=befores[key][0].name,
            after_source=afters[key][0].name,
            tolerance=tolerance,
        )
        for key in befores
        if key in afters
    ]
    unpaired = [
        f"{path.name} ({report.system} on {report.split}) has no partner"
        for keyed, other in ((befores, afters), (afters, befores))
        for key, (path, report) in keyed.items()
        if key not in other
    ]
    return CostReport(
        tolerance=tolerance, baseline=[], changes=changes, unpaired=unpaired, notes=out_notes
    )


# --- rendering --------------------------------------------------------------------------------


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:.4f}"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} s"


def _score(interval: Interval | None) -> str:
    if interval is None:
        return "-"
    return f"{interval.mean:.3f} [{interval.low:.3f}, {interval.high:.3f}]"


def _share(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:+.0f}%"


def _models(models: dict[str, str]) -> str:
    return ", ".join(f"{role} {name}" for role, name in sorted(models.items())) or "-"


def _types(kinds: set[str]) -> list[str]:
    return sorted(
        kinds, key=lambda kind: (TYPE_ORDER.index(kind) if kind in TYPE_ORDER else 99, kind)
    )


def _difference(row: TypeChange) -> str:
    diff = row.difference
    if diff is None:
        return "-"
    return f"{diff.difference:+.3f} [{diff.low:+.3f}, {diff.high:+.3f}]"


def _within(row: TypeChange) -> str:
    return {True: "yes", False: "**no**", None: "-"}[row.within_tolerance]


def render_baseline(report: CostReport) -> str:
    modes = report.baseline
    lines = [
        "# Cost baseline",
        "",
        "Cost is the system's own spend per question per run (without the claim checker and "
        "the judge). Latency is seconds per question per run. Scores are each report's mean "
        "primary metric over its questions, with its own 95% bootstrap interval.",
        "",
        "| Report | System | Split | Runs | Questions | Cost / question | Latency mean | p50 | p90 "
        "| Score [95% CI] | Cache reads |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode in modes:
        latency = mode.latency
        incomplete = "" if mode.complete else " (incomplete)"
        lines.append(
            f"| {mode.source} | {mode.system}{incomplete} | {mode.split} | {mode.runs} | "
            f"{mode.questions} | {_money(mode.cost)} | "
            f"{_seconds(latency.mean if latency else None)} | "
            f"{_seconds(latency.p50 if latency else None)} | "
            f"{_seconds(latency.p90 if latency else None)} | {_score(mode.score)} | "
            f"{_share(mode.tokens.cache_read_share)} |"
        )
    lines += [
        "",
        "Cache reads: the share of input tokens read from the prompt cache; `-` for reports "
        "that recorded no tokens.",
    ]
    for mode in modes:
        kinds = _types(set(mode.cost_by_type) | set(mode.score_by_type))
        lines += [
            "",
            f"## {mode.system} on {mode.split} ({mode.source})",
            "",
            f"Models: {_models(mode.models)}. Measured {mode.created_at[:10]}. "
            f"Total spend ${mode.total_cost_usd:.2f}.",
            "",
            "| Type | Cost / question | Latency mean | Score [95% CI] |",
            "|---|---|---|---|",
        ]
        for kind in kinds:
            lines.append(
                f"| {kind} | {_money(mode.cost_by_type.get(kind))} | "
                f"{_seconds(mode.latency_by_type.get(kind))} | "
                f"{_score(mode.score_by_type.get(kind))} |"
            )
        lines.append(
            f"| **all** | {_money(mode.cost)} | "
            f"{_seconds(mode.latency.mean if mode.latency else None)} | {_score(mode.score)} |"
        )
        lines += _stage_lines(mode.stage_costs, None)
    return _finish(lines, report)


def _stage_lines(before: dict[str, float], after: dict[str, float] | None) -> list[str]:
    stages = sorted(set(before) | set(after or {}))
    if not stages:
        return []
    if after is None:
        lines = ["", "| Stage | Cost / question |", "|---|---|"]
        return lines + [f"| {stage} | {_money(before.get(stage))} |" for stage in stages]
    lines = ["", "| Stage | Before | After |", "|---|---|---|"]
    return lines + [
        f"| {stage} | {_money(before.get(stage))} | {_money(after.get(stage))} |"
        for stage in stages
    ]


def render_changes(report: CostReport) -> str:
    tolerance = report.tolerance
    lines = [
        "# Cost report: before and after",
        "",
        f"A change is **kept** only if, for every question type and overall, the lower bound of "
        f"the 95% interval of the paired difference in the primary metric (after minus before, "
        f"10,000 bootstrap resamples over questions) is above {-tolerance:+.2f}. Cost is the "
        "system's own spend per question per run; cost and latency are compared on the "
        "questions both reports scored.",
        "",
        "| System | Split | Cost before | Cost after | Change | Latency before | Latency after "
        "| Score before | Score after | Difference [95% CI] | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for change in report.changes:
        row, diff = change.overall, change.overall.difference
        lines.append(
            f"| {change.system} | {change.split} | {_money(row.cost_before)} | "
            f"{_money(row.cost_after)} | {_percent(change.cost_change)} | "
            f"{_seconds(row.latency_before)} | {_seconds(row.latency_after)} | "
            f"{'-' if diff is None else f'{diff.mean_a:.3f}'} | "
            f"{'-' if diff is None else f'{diff.mean_b:.3f}'} | {_difference(row)} | "
            f"**{change.verdict}** |"
        )
    for change in report.changes:
        lines += [
            "",
            f"## {change.system} on {change.split}: {change.verdict}",
            "",
            f"Before: {change.before.source} ({change.before.runs} runs, "
            f"{change.before.created_at[:10]}). After: {change.after.source} "
            f"({change.after.runs} runs, {change.after.created_at[:10]}).",
            "",
            *[f"- {reason}" for reason in change.reasons],
            *[f"- Warning: {warning}" for warning in change.warnings],
            "",
            "| Type | Questions | Before | After | Difference [95% CI] | Within tolerance "
            "| Cost before | Cost after | Latency before | Latency after |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in [*change.by_type, change.overall]:
            diff = row.difference
            label = "**all**" if row.type == ALL else row.type
            lines.append(
                f"| {label} | {0 if diff is None else diff.n} | "
                f"{'-' if diff is None else f'{diff.mean_a:.3f}'} | "
                f"{'-' if diff is None else f'{diff.mean_b:.3f}'} | {_difference(row)} | "
                f"{_within(row)} | {_money(row.cost_before)} | {_money(row.cost_after)} | "
                f"{_seconds(row.latency_before)} | {_seconds(row.latency_after)} |"
            )
        shares = (change.before.tokens.cache_read_share, change.after.tokens.cache_read_share)
        if any(share is not None for share in shares):
            lines += [
                "",
                f"Input tokens read from the prompt cache: {_share(shares[0])} before, "
                f"{_share(shares[1])} after.",
            ]
        lines += _stage_lines(change.before.stage_costs, change.after.stage_costs)
    return _finish(lines, report)


def _finish(lines: list[str], report: CostReport) -> str:
    extra = [*report.unpaired, *report.notes]
    if extra:
        lines += ["", *[f"- {note}" for note in extra]]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_cost_report(report: CostReport) -> str:
    return (
        render_changes(report) if report.changes or not report.baseline else render_baseline(report)
    )


# --- an estimate, not a measurement -----------------------------------------------------------


class CachingEstimate(BaseModel):
    """Input cost of one tool conversation's exploring calls, with the static prefix cached
    (as before decision 0024) and with the conversation cached too. Output tokens are the
    same either way and are left out."""

    steps: int
    static_tokens: int
    question_tokens: int
    step_tokens: int
    prefix_only_usd: float
    with_conversation_usd: float

    @property
    def saving(self) -> float:
        """The share of the exploring calls' input cost that conversation caching saves."""
        if self.prefix_only_usd == 0:
            return 0.0
        return 1 - self.with_conversation_usd / self.prefix_only_usd


def estimate_conversation_caching(
    price: ModelPrice,
    *,
    steps: int,
    static_tokens: int,
    step_tokens: int,
    question_tokens: int = 50,
) -> CachingEstimate:
    """What ``steps`` exploring calls cost in input tokens at ``price``, by arithmetic.

    Call ``k`` sends the static prefix (tools and system prompt), the question, and the
    ``k - 1`` earlier steps of ``step_tokens`` each (the model's turn and its tool results).
    With only the prefix cached, call 1 writes the prefix and every later call reads it; the
    conversation is paid in full each time. With the conversation cached too, call 1 writes
    prefix and question, and each later call reads everything the call before it sent and
    writes only its newest step. Every call is assumed to start within the cache's five
    minutes of the one before, and every prefix to be over the model's minimum.
    """
    if steps < 1:
        raise ValueError("An estimate needs at least one step.")
    million = 1_000_000
    prefix_only = with_conversation = 0.0
    for call in range(1, steps + 1):
        conversation = question_tokens + (call - 1) * step_tokens
        if call == 1:
            prefix_only += static_tokens * price.cache_write + conversation * price.input
            with_conversation += (static_tokens + conversation) * price.cache_write
        else:
            prefix_only += static_tokens * price.cache_read + conversation * price.input
            earlier = static_tokens + conversation - step_tokens
            with_conversation += earlier * price.cache_read + step_tokens * price.cache_write
    return CachingEstimate(
        steps=steps,
        static_tokens=static_tokens,
        question_tokens=question_tokens,
        step_tokens=step_tokens,
        prefix_only_usd=prefix_only / million,
        with_conversation_usd=with_conversation / million,
    )


def render_caching_estimate(model: str, estimates: list[CachingEstimate]) -> str:
    first = estimates[0]
    lines = [
        f"# Estimate: caching an exploring conversation on {model}",
        "",
        "**An estimate by arithmetic from the price table, not a measurement.** Input cost of "
        f"the exploring calls of one answer, for a static prefix of about {first.static_tokens:,} "
        f"tokens (tools and system prompt) and about {first.step_tokens:,} tokens added per "
        "step. Output tokens and the final call on the other model are the same either way and "
        "are left out. The measured numbers are live-pass items 20 and 21.",
        "",
        "| Exploring calls | Prefix cached only | Conversation cached too | Saved |",
        "|---|---|---|---|",
    ]
    for estimate in estimates:
        lines.append(
            f"| {estimate.steps} | ${estimate.prefix_only_usd:.5f} | "
            f"${estimate.with_conversation_usd:.5f} | {100 * estimate.saving:.0f}% |"
        )
    return "\n".join(lines) + "\n"
