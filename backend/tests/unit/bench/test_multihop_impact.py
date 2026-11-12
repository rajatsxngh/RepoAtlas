from __future__ import annotations

import pytest

from repoatlas.bench import generate
from repoatlas.bench.facts import Facts
from repoatlas.bench.generate import (
    gen_multihop,
    gen_static_impact,
    reaching_tests,
    transitive_callers,
)
from repoatlas.bench.ownership import Ownership, log_args, parse_log
from repoatlas.bench.schema import AnswerSetTruth, ImpactTruth

CORE = "py:src/mini/core.py:"
UTIL = "py:src/mini/util.py:"
TESTS = "py:tests/test_core.py:"
MARK = "\x1ecommit\x1f"


def test_tests_reaching_allows_one_intermediate(minipy: Facts) -> None:
    assert reaching_tests(minipy, UTIL + "helper:function") == [
        TESTS + "test_entry:test",
        TESTS + "test_twice:test",
    ]
    # make: test_make calls it directly; entry is the intermediate for test_entry.
    assert reaching_tests(minipy, CORE + "make:function") == [
        TESTS + "test_entry:test",
        TESTS + "test_make:test",
    ]


def test_transitive_callers_stop_at_depth(minipy: Facts) -> None:
    helper = UTIL + "helper:function"
    assert transitive_callers(minipy, helper, depth=1) == [
        CORE + "Base.__init__:method",
        CORE + "Base.run:method",
        CORE + "entry:function",
        UTIL + "twice:function",
    ]
    assert transitive_callers(minipy, helper) == sorted(
        [
            CORE + "Base.__init__:method",
            CORE + "Base.run:method",
            CORE + "entry:function",
            UTIL + "twice:function",
            CORE + "make:function",
            CORE + "Child.run:method",
            TESTS + "test_entry:test",
            TESTS + "test_twice:test",
            TESTS + "test_make:test",
            TESTS + "test_child:test",
        ]
    )
    assert transitive_callers(minipy, TESTS + "test_entry:test") == []


def test_gen_multihop_without_history(minipy: Facts) -> None:
    questions = gen_multihop(minipy, seed=1)
    assert questions
    for q in questions:
        assert isinstance(q.ground_truth, AnswerSetTruth)
        assert q.provenance == "scip" and q.notes.startswith("template: tests-reach-1")
        assert all(a.symbol_key.endswith(":test") for a in q.ground_truth.answers)


def test_parse_log_counts_non_merge_commits_per_file() -> None:
    log = (
        f"{MARK}Ada\n\na.py\nb.py\n"
        f"{MARK}Ada\n\na.py\n"
        f"{MARK}Bob\n\na.py\nb.py\n"
        f"{MARK}Ada\n\na.py\ngone.py\n"
        f"{MARK}Bob\n\nb.py\n"
    )
    own = parse_log(log, ["a.py", "b.py"])
    assert own.commits == 5
    assert own.file_commits == {"a.py": 4, "b.py": 3}
    assert own.owners == {"a.py": "Ada", "b.py": "Bob"}  # Ada 3/4, Bob 2/3
    tie = parse_log(f"{MARK}Ada\n\nc.py\n{MARK}Bob\n\nc.py\n{MARK}Cy\n\nc.py\n", ["c.py"])
    assert tie.owners == {}  # nobody has more than half
    few = parse_log(f"{MARK}Ada\n\nd.py\n{MARK}Ada\n\nd.py\n", ["d.py"])
    assert few.owners == {}  # fewer than MIN_COMMITS


def test_log_args_never_read_emails() -> None:
    args = log_args("c" * 40)
    assert "--no-merges" in args and "--no-renames" in args
    assert not any("%ae" in a or "%ce" in a for a in args)


def test_owner_modules(minipy: Facts, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generate, "MODULES_MIN", 1)
    own = Ownership(commits=5, owners={"src/mini/util.py": "Ada"}, file_commits={})
    questions = gen_multihop(minipy, seed=1, ownership=own, count=2)
    owner = [q for q in questions if q.provenance == "git"]
    assert len(owner) == 1
    truth = owner[0].ground_truth
    assert isinstance(truth, AnswerSetTruth)
    # core.py uses util.py's code; the test file is not a library module.
    assert [a.symbol_key for a in truth.answers] == ["file:src/mini/core.py"]
    assert "Ada" in owner[0].text and "src/mini/util.py" in owner[0].notes


def test_gen_static_impact(minipy: Facts) -> None:
    questions = gen_static_impact(minipy, seed=1, count=10)
    assert questions
    for q in questions:
        assert isinstance(q.ground_truth, ImpactTruth)
        target = q.ground_truth.target.symbol_key
        assert [s.symbol_key for s in q.ground_truth.static] == transitive_callers(minipy, target)
        assert q.ground_truth.real_failing_tests is None


def test_gen_static_impact_with_given_targets(minipy: Facts) -> None:
    targets = [UTIL + "twice:function", "py:nope.py:x:function"]
    questions = gen_static_impact(minipy, seed=1, targets=targets)
    assert [q.ground_truth.target.symbol_key for q in questions] == [UTIL + "twice:function"]  # type: ignore[union-attr]
