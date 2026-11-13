"""Tuning the impact parameters on the dev split (M11 T9).

Every grid point (``max_depth`` x ``decay`` x ``min_score``) runs the engine on each dev impact
question and is scored with the benchmark scorer the runner uses (``score_benchmark``): static
F1 against SCIP-derived impact sets and, where the target was mutated in the sandbox,
test-level F1 and recall@10 against the tests that really failed. The objective is the mean of
the two means (static F1 alone when no dev question has real failures). Ties go to the smaller
search (shallower, then faster decay, then higher ``min_score``), which costs less and
overfits less. Only the dev split may be tuned on.
"""

from __future__ import annotations

import itertools
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from repoatlas.bench.schema import ImpactTruth
from repoatlas.evals.outputs import SystemOutput
from repoatlas.evals.registry import EvalItem, ImpactTarget
from repoatlas.evals.scorers import score_benchmark
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.schema import ImpactParams, SymbolTarget
from repoatlas.impact.system import to_prediction

DEPTHS = (1, 2, 3, 4, 5)
DECAYS = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
MIN_SCORES = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3)


class QuestionScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: str
    static_f1: float
    static_precision: float | None
    static_recall: float | None
    real_f1: float | None = None
    real_recall_at_10: float | None = None
    items: int


class SweepPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_depth: int
    decay: float
    min_score: float
    static_f1: float
    static_precision: float | None
    static_recall: float | None
    real_f1: float | None
    real_recall_at_10: float | None
    objective: float
    questions: list[QuestionScore]

    def params(self, base: ImpactParams | None = None) -> ImpactParams:
        base = base or ImpactParams()
        return base.model_copy(
            update={"max_depth": self.max_depth, "decay": self.decay, "min_score": self.min_score}
        )


@dataclass(frozen=True)
class Grid:
    depths: Sequence[int] = DEPTHS
    decays: Sequence[float] = DECAYS
    min_scores: Sequence[float] = MIN_SCORES

    def points(self) -> list[tuple[int, float, float]]:
        return list(itertools.product(self.depths, self.decays, self.min_scores))


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def score_point(
    engine: ImpactEngine,
    items: Sequence[EvalItem],
    snapshot_for: Callable[[EvalItem], int],
    params: ImpactParams,
) -> SweepPoint:
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
        output = SystemOutput(kind="impact", impact=prediction.ranked())
        score = score_benchmark(item, output)
        metrics = score.metrics
        questions.append(
            QuestionScore(
                question_id=item.id,
                static_f1=score.primary or 0.0,
                static_precision=metrics.get("static_precision"),
                static_recall=metrics.get("static_recall"),
                real_f1=metrics.get("real_f1"),
                real_recall_at_10=metrics.get("real_recall_at_10"),
                items=len(prediction.items),
            )
        )
    static = _mean(q.static_f1 for q in questions) or 0.0
    real = _mean(q.real_f1 for q in questions)
    objective = static if real is None else (static + real) / 2
    return SweepPoint(
        max_depth=params.max_depth,
        decay=params.decay,
        min_score=params.min_score,
        static_f1=static,
        static_precision=_mean(q.static_precision for q in questions),
        static_recall=_mean(q.static_recall for q in questions),
        real_f1=real,
        real_recall_at_10=_mean(q.real_recall_at_10 for q in questions),
        objective=objective,
        questions=questions,
    )


def rank_key(point: SweepPoint) -> tuple[float, int, float, float]:
    return (-round(point.objective, 9), point.max_depth, point.decay, -point.min_score)


class SweepResult(BaseModel):
    split: str
    questions: list[str]
    points: list[SweepPoint]  # best first
    default: SweepPoint  # the parameters before tuning

    @property
    def best(self) -> SweepPoint:
        return self.points[0]


def sweep(
    engine: ImpactEngine,
    items: Sequence[EvalItem],
    snapshot_for: Callable[[EvalItem], int],
    *,
    grid: Grid | None = None,
    split: str = "dev",
    base: ImpactParams | None = None,
    progress: Callable[[SweepPoint], None] | None = None,
) -> SweepResult:
    if split != "dev":
        raise ValueError("Impact parameters are tuned on the dev split only.")
    base = base or ImpactParams()
    impact_items = [item for item in items if isinstance(item.truth, ImpactTruth)]
    points = []
    for depth, decay, min_score in (grid or Grid()).points():
        params = base.model_copy(
            update={"max_depth": depth, "decay": decay, "min_score": min_score}
        )
        point = score_point(engine, impact_items, snapshot_for, params)
        points.append(point)
        if progress is not None:
            progress(point)
    default = score_point(engine, impact_items, snapshot_for, base)
    return SweepResult(
        split=split,
        questions=[item.id for item in impact_items],
        points=sorted(points, key=rank_key),
        default=default,
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def sweep_markdown(result: SweepResult, top: int = 15) -> str:
    lines = [
        f"# Impact parameter sweep ({result.split})",
        "",
        f"{len(result.points)} grid points over {len(result.questions)} impact questions: "
        + ", ".join(f"`{q}`" for q in result.questions)
        + ".",
        "",
        "Objective: mean of static F1 and real-failure F1 (static F1 alone without real data).",
        "",
        "| Rank | max_depth | decay | min_score | objective | static F1 | static P | static R "
        "| real F1 | real R@10 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def row(label: str, point: SweepPoint) -> str:
        return (
            f"| {label} | {point.max_depth} | {point.decay} | {point.min_score} | "
            f"{_fmt(point.objective)} | {_fmt(point.static_f1)} | {_fmt(point.static_precision)} "
            f"| {_fmt(point.static_recall)} | {_fmt(point.real_f1)} "
            f"| {_fmt(point.real_recall_at_10)} |"
        )

    for rank, point in enumerate(result.points[:top], start=1):
        lines.append(row(str(rank), point))
    lines.append(row("default", result.default))
    best = result.best
    lines += [
        "",
        "## Best point, per question",
        "",
        "| Question | static F1 | static P | static R | real F1 | real R@10 | items |",
        "|---|---|---|---|---|---|---|",
    ]
    for q in best.questions:
        lines.append(
            f"| `{q.question_id}` | {_fmt(q.static_f1)} | {_fmt(q.static_precision)} | "
            f"{_fmt(q.static_recall)} | {_fmt(q.real_f1)} | {_fmt(q.real_recall_at_10)} "
            f"| {q.items} |"
        )
    return "\n".join(lines) + "\n"
