"""Baseline runs (SBX-5): which tests are stable enough to serve as ground truth.

The full suite runs ``runs`` times on the unmodified repo image. A test whose outcome differs
across runs (or that is missing from some run) is **quarantined**; a test that fails or errors
in every run is **pre-failing**. Both are excluded from mutation ground truth and written to the
recipe's ``quarantine`` list. ``check`` runs the suite once more and compares it with the
stable outcomes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from repoatlas.sandbox.runner import SandboxRunner, TestRun

MISSING = "missing"
FAILING = frozenset({"failed", "error"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BaselineRun(_Model):
    duration_s: float
    exit_code: int | None
    counts: dict[str, int]
    collection_errors: list[str]
    report_error: str | None = None


class BaselineCheck(_Model):
    duration_s: float
    compared: int
    mismatches: dict[str, str]  # test ID → "expected → got"

    @property
    def matches(self) -> bool:
        return not self.mismatches


class Baseline(_Model):
    repo_id: str
    commit: str
    image_digest: str
    runs: list[BaselineRun]
    outcomes: dict[str, str]  # stable tests: test ID → outcome (same in every run)
    flaky: dict[str, list[str]]  # quarantined: test ID → outcome per run
    quarantined: list[str]
    pre_failing: list[str]
    collection_errors: list[str]  # suite-level errors seen in any run
    check: BaselineCheck | None = None

    @property
    def excluded(self) -> set[str]:
        return set(self.quarantined) | set(self.pre_failing)

    def recipe_quarantine(self) -> list[str]:
        return sorted(self.excluded)


def summarize_run(run: TestRun) -> BaselineRun:
    counts = Counter(run.outcomes().values())
    return BaselineRun(
        duration_s=run.duration_s,
        exit_code=run.exit_code,
        counts=dict(sorted(counts.items())),
        collection_errors=sorted(r.test_id for r in run.collection_errors),
        report_error=run.report_error,
    )


def classify(
    runs: Sequence[dict[str, str]],
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """Per-run outcome maps → (stable outcomes, flaky outcomes, pre-failing IDs)."""
    every = sorted(set().union(*runs)) if runs else []
    stable: dict[str, str] = {}
    flaky: dict[str, list[str]] = {}
    pre_failing: list[str] = []
    for test_id in every:
        seen = [run.get(test_id, MISSING) for run in runs]
        if len(set(seen)) > 1:
            flaky[test_id] = seen
            continue
        stable[test_id] = seen[0]
        if seen[0] in FAILING:
            pre_failing.append(test_id)
    return stable, flaky, pre_failing


def run_baseline(
    runner: SandboxRunner,
    *,
    image_digest: str,
    runs: int = 3,
    progress: Callable[[int, TestRun], None] | None = None,
) -> Baseline:
    if runs < 2:
        raise ValueError("A baseline needs at least two runs to find flaky tests.")
    results: list[TestRun] = []
    for index in range(runs):
        run = runner.run_tests()
        if run.report_error:
            raise RuntimeError(
                f"Baseline run {index + 1} failed: {run.report_error}\n{run.log_tail}"
            )
        results.append(run)
        if progress is not None:
            progress(index + 1, run)
    stable, flaky, pre_failing = classify([run.outcomes() for run in results])
    collection = sorted({r.test_id for run in results for r in run.collection_errors})
    return Baseline(
        repo_id=runner.recipe.id,
        commit=runner.recipe.commit,
        image_digest=image_digest,
        runs=[summarize_run(run) for run in results],
        outcomes=stable,
        flaky=flaky,
        quarantined=sorted(flaky),
        pre_failing=pre_failing,
        collection_errors=collection,
    )


def compare(baseline: Baseline, outcomes: dict[str, str]) -> dict[str, str]:
    """Stable tests whose outcome in ``outcomes`` differs from the baseline's."""
    mismatches: dict[str, str] = {}
    for test_id, expected in baseline.outcomes.items():
        got = outcomes.get(test_id, MISSING)
        if got != expected:
            mismatches[test_id] = f"{expected} → {got}"
    for test_id in sorted(set(outcomes) - set(baseline.outcomes) - set(baseline.flaky)):
        mismatches[test_id] = f"{MISSING} → {outcomes[test_id]}"
    return dict(sorted(mismatches.items()))


def check_baseline(runner: SandboxRunner, baseline: Baseline) -> Baseline:
    """Run once more and record whether every stable outcome repeats."""
    run = runner.run_tests()
    if run.report_error:
        raise RuntimeError(f"Check run failed: {run.report_error}\n{run.log_tail}")
    check = BaselineCheck(
        duration_s=run.duration_s,
        compared=len(baseline.outcomes),
        mismatches=compare(baseline, run.outcomes()),
    )
    return baseline.model_copy(update={"check": check})
