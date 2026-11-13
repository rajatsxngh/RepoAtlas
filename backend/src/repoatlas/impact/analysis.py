"""What the ranked list misses (M11 T10): the unknown bucket's hit rate, and test files.

For each impact question with real failures (sandbox mutations), the engine's report is split
three ways against the tests that really failed:

- **ranked:** failing tests among the report's ranked test items;
- **unknown only:** failing tests that are not ranked but enclose an unknown site (an unresolved
  call or a string literal naming the target) — the bucket's hit rate;
- **missed:** neither.

It also scores predictions at the level of test files (a failing test's file is predicted when
any ranked test item, or a ranked test file, lies in it), the open question of the milestone.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from repoatlas.bench.schema import ImpactTruth
from repoatlas.evals.registry import EvalItem
from repoatlas.evals.scorers import set_prf
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.schema import ImpactParams, SymbolTarget
from repoatlas.parsing.symbols import parse_symbol_key


class UnknownQuestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: str
    repo_id: str
    failing: int  # confirmed failing tests with a symbol key
    ranked: int
    unknown_only: int
    missed: int
    unknown_sites: int
    file_precision: float | None
    file_recall: float | None
    file_f1: float | None


class UnknownReport(BaseModel):
    split: str
    params: ImpactParams
    questions: list[UnknownQuestion]
    failing: int
    ranked: int
    unknown_only: int
    missed: int
    unknown_hit_rate: float | None  # unknown-only ÷ failing (pooled)
    questions_helped: int  # questions where the bucket holds at least one failure
    file_precision: float | None
    file_recall: float | None
    file_f1: float | None


def _path(key: str) -> str | None:
    if key.startswith("file:"):
        return key[len("file:") :]
    try:
        return parse_symbol_key(key).path
    except ValueError:
        return None


def unknown_report(
    engine: ImpactEngine,
    items: Sequence[EvalItem],
    snapshot_for: Callable[[EvalItem], int],
    *,
    split: str,
    params: ImpactParams | None = None,
) -> UnknownReport:
    params = params or engine.params
    questions = []
    for item in items:
        truth = item.truth
        if not isinstance(truth, ImpactTruth) or not truth.real_failing_tests:
            continue
        failing = {t.symbol_key for t in truth.real_failing_tests if t.symbol_key}
        report = engine.analyze(
            snapshot_for(item), SymbolTarget(symbol_key=truth.target.symbol_key), params
        )
        tests = report.group("test")
        ranked = failing & {i.symbol_key for i in tests}
        enclosing = {s.site.symbol_key for s in report.unknown if s.site.symbol_key}
        unknown_only = (failing - ranked) & enclosing
        failing_files = {p for key in failing if (p := _path(key))}
        predicted_files = sorted({p for i in tests if (p := _path(i.symbol_key))})
        files = set_prf(predicted_files, failing_files)
        questions.append(
            UnknownQuestion(
                question_id=item.id,
                repo_id=item.repo_id,
                failing=len(failing),
                ranked=len(ranked),
                unknown_only=len(unknown_only),
                missed=len(failing) - len(ranked) - len(unknown_only),
                unknown_sites=len(report.unknown),
                file_precision=files.precision,
                file_recall=files.recall,
                file_f1=files.f1,
            )
        )

    def mean(values: list[float | None]) -> float | None:
        present = [v for v in values if v is not None]
        return statistics.fmean(present) if present else None

    failing_total = sum(q.failing for q in questions)
    unknown_total = sum(q.unknown_only for q in questions)
    return UnknownReport(
        split=split,
        params=params,
        questions=questions,
        failing=failing_total,
        ranked=sum(q.ranked for q in questions),
        unknown_only=unknown_total,
        missed=sum(q.missed for q in questions),
        unknown_hit_rate=unknown_total / failing_total if failing_total else None,
        questions_helped=sum(1 for q in questions if q.unknown_only > 0),
        file_precision=mean([q.file_precision for q in questions]),
        file_recall=mean([q.file_recall for q in questions]),
        file_f1=mean([q.file_f1 for q in questions]),
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def unknown_markdown(report: UnknownReport) -> str:
    lines = [
        f"# Impact: unknown bucket and test files ({report.split})",
        "",
        f"Parameters: max_depth {report.params.max_depth}, decay {report.params.decay}, "
        f"min_score {report.params.min_score}. Questions with real failures: "
        f"{len(report.questions)}.",
        "",
        f"- Confirmed failing tests: {report.failing}; ranked {report.ranked}, "
        f"in the unknown bucket only {report.unknown_only}, missed {report.missed}.",
        f"- Unknown-bucket hit rate (failures found only there): "
        f"{_fmt(report.unknown_hit_rate)}; questions it helped: {report.questions_helped}.",
        f"- Test-file level (mean over questions): precision {_fmt(report.file_precision)}, "
        f"recall {_fmt(report.file_recall)}, F1 {_fmt(report.file_f1)}.",
        "",
        "| Question | failing | ranked | unknown only | missed | unknown sites | file P | file R |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for q in report.questions:
        lines.append(
            f"| `{q.question_id}` | {q.failing} | {q.ranked} | {q.unknown_only} | {q.missed} | "
            f"{q.unknown_sites} | {_fmt(q.file_precision)} | {_fmt(q.file_recall)} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------
# Split report: precision, recall, and recall@k with 95% intervals
# ---------------------------------------------------------------------------------------------

REAL_KS = (5, 10, 20)
STATIC_METRICS = ("static_precision", "static_recall", "static_f1")
REAL_METRICS = (
    "real_precision",
    "real_recall",
    "real_f1",
    *(f"real_recall_at_{k}" for k in REAL_KS),
)
METRIC_NAMES = {
    "static_precision": ("Static (SCIP)", "precision"),
    "static_recall": ("Static (SCIP)", "recall"),
    "static_f1": ("Static (SCIP)", "F1"),
    "real_precision": ("Real (sandbox failures), test level", "precision"),
    "real_recall": ("Real (sandbox failures), test level", "recall"),
    "real_f1": ("Real (sandbox failures), test level", "F1"),
    **{
        f"real_recall_at_{k}": ("Real (sandbox failures), test level", f"recall@{k}")
        for k in REAL_KS
    },
}


class SplitQuestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: str
    repo_id: str
    predicted: int
    predicted_tests: int
    static_truth: int
    failing: int | None
    metrics: dict[str, float | None]


class MetricInterval(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    questions: int
    mean: float | None
    low: float | None
    high: float | None


class SplitReport(BaseModel):
    split: str
    params: ImpactParams
    questions: list[SplitQuestion]
    intervals: list[MetricInterval]

    def interval(self, metric: str) -> MetricInterval:
        return next(i for i in self.intervals if i.metric == metric)


def split_report(
    engine: ImpactEngine,
    items: Sequence[EvalItem],
    snapshot_for: Callable[[EvalItem], int],
    *,
    split: str,
    params: ImpactParams | None = None,
) -> SplitReport:
    """Scores every impact question of ``split`` the way the runner does (static and real
    precision, recall, F1), plus real recall@5/@10/@20, with 95% bootstrap intervals over
    questions. The engine is deterministic, so one run stands for every run."""
    from repoatlas.evals.outputs import SystemOutput
    from repoatlas.evals.registry import ImpactTarget
    from repoatlas.evals.scorers import recall_at_k, score_benchmark
    from repoatlas.evals.stats import bootstrap_ci
    from repoatlas.impact.system import to_prediction

    params = params or engine.params
    questions = []
    for item in items:
        truth = item.truth
        if not isinstance(truth, ImpactTruth):
            continue
        target = ImpactTarget(symbol_key=truth.target.symbol_key)
        report = engine.analyze(
            snapshot_for(item),
            SymbolTarget(symbol_key=truth.target.symbol_key),
            params,
            unknown=False,
        )
        prediction = to_prediction(report, target)
        ranked = prediction.ranked()
        score = score_benchmark(item, SystemOutput(kind="impact", impact=ranked))
        metrics: dict[str, float | None] = {
            "static_precision": score.metrics.get("static_precision"),
            "static_recall": score.metrics.get("static_recall"),
            "static_f1": score.primary,
        }
        tests = [i.symbol_key for i in ranked if i.group == "test"]
        failing: set[str] | None = None
        if truth.real_failing_tests is not None:
            failing = {t.symbol_key for t in truth.real_failing_tests if t.symbol_key}
            for name in ("real_precision", "real_recall", "real_f1"):
                metrics[name] = score.metrics.get(name)
            for k in REAL_KS:
                metrics[f"real_recall_at_{k}"] = recall_at_k(tests, failing, k)
        questions.append(
            SplitQuestion(
                question_id=item.id,
                repo_id=item.repo_id,
                predicted=len(ranked),
                predicted_tests=len(tests),
                static_truth=len(truth.static),
                failing=None if failing is None else len(failing),
                metrics=metrics,
            )
        )
    intervals = []
    for metric in (*STATIC_METRICS, *REAL_METRICS):
        values = [v for q in questions if (v := q.metrics.get(metric)) is not None]
        ci = bootstrap_ci(values)
        intervals.append(
            MetricInterval(
                metric=metric,
                questions=len(values),
                mean=None if ci is None else ci.mean,
                low=None if ci is None else ci.low,
                high=None if ci is None else ci.high,
            )
        )
    return SplitReport(split=split, params=params, questions=questions, intervals=intervals)


def _ci(interval: MetricInterval) -> str:
    if interval.mean is None:
        return "-"
    return f"{interval.mean:.3f} [{_fmt(interval.low)}, {_fmt(interval.high)}]"


def split_markdown(report: SplitReport) -> str:
    params = report.params
    lines = [
        f"# impact_engine: static and real impact ({report.split})",
        "",
        f"Parameters (decision 0019): max_depth {params.max_depth}, decay {params.decay}, "
        f"min_score {params.min_score}, limit {params.limit}. Means over questions with 95% "
        "bootstrap intervals (10,000 resamples). The engine is deterministic, so its three "
        "runner runs are identical.",
        "",
        "| Ground truth | Metric | Questions | Mean [95% CI] |",
        "|---|---|---|---|",
    ]
    for interval in report.intervals:
        source, metric = METRIC_NAMES[interval.metric]
        lines.append(f"| {source} | {metric} | {interval.questions} | {_ci(interval)} |")
    lines += [
        "",
        "| Question | predicted | tests | static truth | failing | static P | static R "
        "| real P | real R | R@10 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for q in report.questions:
        m = q.metrics
        failing = "-" if q.failing is None else str(q.failing)
        lines.append(
            f"| `{q.question_id}` | {q.predicted} | {q.predicted_tests} | {q.static_truth} | "
            f"{failing} | {_fmt(m.get('static_precision'))} | {_fmt(m.get('static_recall'))} | "
            f"{_fmt(m.get('real_precision'))} | {_fmt(m.get('real_recall'))} | "
            f"{_fmt(m.get('real_recall_at_10'))} |"
        )
    return "\n".join(lines) + "\n"
