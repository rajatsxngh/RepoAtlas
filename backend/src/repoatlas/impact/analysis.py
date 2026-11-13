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
