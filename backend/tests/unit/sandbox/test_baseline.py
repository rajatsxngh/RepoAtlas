from __future__ import annotations

from repoatlas.sandbox.baseline import Baseline, classify, compare


def test_classify_separates_stable_flaky_and_pre_failing() -> None:
    runs = [
        {"a": "passed", "b": "failed", "c": "passed", "d": "error"},
        {"a": "passed", "b": "failed", "c": "failed", "d": "error"},
        {"a": "passed", "b": "failed", "d": "error"},
    ]
    stable, flaky, pre_failing = classify(runs)
    assert stable == {"a": "passed", "b": "failed", "d": "error"}
    assert flaky == {"c": ["passed", "failed", "missing"]}
    assert pre_failing == ["b", "d"]


def _baseline() -> Baseline:
    return Baseline(
        repo_id="r",
        commit="c" * 40,
        image_digest="sha256:x",
        runs=[],
        outcomes={"a": "passed", "b": "failed"},
        flaky={"c": ["passed", "failed"]},
        quarantined=["c"],
        pre_failing=["b"],
        collection_errors=[],
    )


def test_compare_ignores_quarantined_tests() -> None:
    baseline = _baseline()
    assert compare(baseline, {"a": "passed", "b": "failed", "c": "skipped"}) == {}
    assert compare(baseline, {"a": "failed", "b": "failed", "e": "passed"}) == {
        "a": "passed → failed",
        "e": "missing → passed",
    }
    assert compare(baseline, {"b": "failed"}) == {"a": "passed → missing"}


def test_recipe_quarantine_holds_flaky_and_pre_failing() -> None:
    assert _baseline().recipe_quarantine() == ["b", "c"]
