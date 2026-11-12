from __future__ import annotations

from repoatlas.bench.facts import Facts
from repoatlas.bench.generate import confirmed_targets, gen_static_impact, import_real_impact
from repoatlas.bench.schema import ImpactTruth
from repoatlas.sandbox.mutations import FailingTest, MutationRecord

UTIL = "py:src/mini/util.py:"
TESTS = "py:tests/test_core.py:"


def record(
    key: str, status: str = "confirmed", failing: list[FailingTest] | None = None
) -> MutationRecord:
    return MutationRecord(
        symbol_key=key,
        path=key.split(":")[1],
        kind="raise",
        seed=7,
        stratum=0,
        fan_in=1,
        hops=1,
        status=status,  # type: ignore[arg-type]
        failing=failing or [],
    )


RECORDS = [
    record(
        UTIL + "twice:function",
        failing=[
            FailingTest(
                test_id="tests/test_core.py::test_twice", symbol_key=TESTS + "test_twice:test"
            ),
            FailingTest(test_id="tests/test_core.py::test_child[1]", symbol_key=None),
        ],
    ),
    record(UTIL + "helper:function", status="no_failures"),
    record(UTIL + "unused:function", status="skipped"),
]


def test_confirmed_targets() -> None:
    assert confirmed_targets(RECORDS) == [UTIL + "twice:function"]


def test_import_real_impact(minipy: Facts) -> None:
    questions = gen_static_impact(minipy, seed=1, count=10)
    assert any(q.ground_truth.target.symbol_key == UTIL + "twice:function" for q in questions)  # type: ignore[union-attr]
    joined, stats = import_real_impact(questions, RECORDS)
    assert (stats.joined, stats.failing_tests, stats.unmapped_tests) == (1, 2, 1)
    for before, after in zip(questions, joined, strict=True):
        assert isinstance(after.ground_truth, ImpactTruth)
        if after.ground_truth.target.symbol_key == UTIL + "twice:function":
            tests = after.ground_truth.real_failing_tests
            assert tests is not None
            assert [t.test_id for t in tests] == [
                "tests/test_core.py::test_child[1]",
                "tests/test_core.py::test_twice",
            ]
            assert after.provenance == "sandbox"
            assert "2 confirmed failing tests" in after.notes and "1 unmapped" in after.notes
            assert after.ground_truth.static == before.ground_truth.static  # type: ignore[union-attr]
        else:
            assert after == before


def test_targets_without_confirmed_failures_stay_static(minipy: Facts) -> None:
    questions = gen_static_impact(minipy, seed=1, count=10)
    joined, stats = import_real_impact(questions, RECORDS[1:])
    assert joined == questions and stats.joined == 0
