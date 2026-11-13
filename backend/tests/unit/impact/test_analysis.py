"""The unknown-bucket hit rate and test-file scores (T10)."""

from __future__ import annotations

from repoatlas.bench.schema import ImpactTruth, Location, RealFailingTest
from repoatlas.evals.registry import EvalItem
from repoatlas.impact.analysis import unknown_markdown, unknown_report
from repoatlas.impact.data import MemoryImpactData
from repoatlas.impact.engine import ImpactEngine
from tests.unit.graph.helpers import graph_of

FILES = {
    "lib.py": "def target(x):\n    return x\n\n\ndef helper(x):\n    return target(x)\n",
    "tests/test_lib.py": (
        "from lib import helper\n\n\n"
        "def test_helper():\n    assert helper(1) == 1\n\n\n"
        "def test_dynamic(obj):\n    assert obj.target(1) == 1\n\n\n"
        "def test_unseen(fixture):\n    assert fixture == 1\n"
    ),
}
TARGET = "py:lib.py:target:function"
HELPER_TEST = "py:tests/test_lib.py:test_helper:test"
DYNAMIC_TEST = "py:tests/test_lib.py:test_dynamic:test"
UNSEEN_TEST = "py:tests/test_lib.py:test_unseen:test"


def item(failing: list[str] | None) -> EvalItem:
    return EvalItem(
        id="l-impact-001",
        type="impact",
        text="What breaks if I change `target` (defined in `lib.py`)?",
        repo_id="l",
        commit="c" * 40,
        split="test",
        truth=ImpactTruth(
            target=Location(symbol_key=TARGET, path="lib.py", line=1),
            static=[],
            real_failing_tests=None
            if failing is None
            else [RealFailingTest(test_id=key, symbol_key=key) for key in failing],
        ),
    )


def test_failures_are_split_into_ranked_unknown_only_and_missed() -> None:
    engine = ImpactEngine(MemoryImpactData(graph_of(FILES), FILES))
    report = unknown_report(
        engine,
        [item([HELPER_TEST, DYNAMIC_TEST, UNSEEN_TEST]), item(None)],
        lambda _i: 1,
        split="test",
    )
    (q,) = report.questions  # the question without real failures is skipped
    assert (q.failing, q.ranked, q.unknown_only, q.missed) == (3, 1, 1, 1)
    assert report.unknown_hit_rate == 1 / 3
    assert report.questions_helped == 1
    assert q.file_recall == 1.0 and q.file_precision == 1.0
    assert "only 1" in unknown_markdown(report)


def test_the_split_report_scores_static_and_real_with_intervals() -> None:
    from repoatlas.impact.analysis import split_markdown, split_report

    engine = ImpactEngine(MemoryImpactData(graph_of(FILES), FILES))
    question = item([HELPER_TEST, DYNAMIC_TEST])
    report = split_report(engine, [question, item(None)], lambda _i: 1, split="test")
    assert len(report.questions) == 2
    real = report.interval("real_recall")
    assert real.questions == 1 and real.mean == 0.5  # helper's test ranked, the dynamic one not
    assert report.interval("real_recall_at_5").mean == 0.5
    static = report.interval("static_f1")
    assert static.questions == 2 and static.low is not None and static.high is not None
    text = split_markdown(report)
    assert "recall@20" in text and "| Static (SCIP) | F1 | 2 |" in text
