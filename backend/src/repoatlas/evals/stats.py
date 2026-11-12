"""Statistics for reports (EVL-5, decision 0013).

- Each question's score is its mean over the runs that produced one (``question_means``).
- ``bootstrap_ci`` — a percentile bootstrap interval for the mean over questions.
- ``paired_bootstrap`` — the difference between two systems on the same questions: resample
  questions with replacement (10,000 times, fixed seed), take the mean of the per-question
  differences, and report the 2.5th and 97.5th percentiles.

Resampling uses ``random.Random(seed)`` so intervals are identical on every machine.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

RESAMPLES = 10_000
SEED = 20261112
CONFIDENCE = 0.95


class Interval(BaseModel):
    model_config = ConfigDict(frozen=True)

    mean: float
    low: float
    high: float
    n: int


def question_means(scores: Iterable[tuple[str, float | None]]) -> dict[str, float]:
    """``(question ID, score)`` pairs → each question's mean score (``None`` ignored)."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for question_id, value in scores:
        if value is not None:
            grouped[question_id].append(value)
    return {qid: statistics.fmean(values) for qid, values in sorted(grouped.items())}


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _bootstrap(values: Sequence[float], resamples: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return means


def bootstrap_ci(
    values: Sequence[float], *, resamples: int = RESAMPLES, seed: int = SEED
) -> Interval | None:
    if not values:
        return None
    mean = statistics.fmean(values)
    if len(values) == 1:
        return Interval(mean=mean, low=mean, high=mean, n=1)
    means = _bootstrap(values, resamples, seed)
    alpha = (1 - CONFIDENCE) / 2
    return Interval(
        mean=mean, low=_percentile(means, alpha), high=_percentile(means, 1 - alpha), n=len(values)
    )


class PairedDifference(BaseModel):
    """``b - a`` over the questions both systems scored."""

    model_config = ConfigDict(frozen=True)

    mean_a: float
    mean_b: float
    difference: float
    low: float
    high: float
    n: int
    p_b_better: float  # share of resamples where b's mean is higher

    @property
    def significant(self) -> bool:
        return self.low > 0 or self.high < 0


def paired_bootstrap(
    a: Mapping[str, float],
    b: Mapping[str, float],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> PairedDifference | None:
    shared = sorted(set(a) & set(b))
    if not shared:
        return None
    diffs = [b[qid] - a[qid] for qid in shared]
    mean_a = statistics.fmean(a[qid] for qid in shared)
    mean_b = statistics.fmean(b[qid] for qid in shared)
    difference = statistics.fmean(diffs)
    if len(diffs) == 1:
        better = 1.0 if difference > 0 else 0.0
        return PairedDifference(
            mean_a=mean_a,
            mean_b=mean_b,
            difference=difference,
            low=difference,
            high=difference,
            n=1,
            p_b_better=better,
        )
    means = _bootstrap(diffs, resamples, seed)
    alpha = (1 - CONFIDENCE) / 2
    return PairedDifference(
        mean_a=mean_a,
        mean_b=mean_b,
        difference=difference,
        low=_percentile(means, alpha),
        high=_percentile(means, 1 - alpha),
        n=len(diffs),
        p_b_better=sum(m > 0 for m in means) / len(means),
    )
