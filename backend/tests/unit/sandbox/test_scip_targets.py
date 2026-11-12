from __future__ import annotations

from pathlib import Path

import pytest

from repoatlas.parsing.models import ParsedFile
from repoatlas.sandbox.parsed import parse_bytes
from repoatlas.sandbox.scip import (
    Document,
    Occurrence,
    ScipError,
    ScipIndex,
    encode_index,
    read_index,
)
from repoatlas.sandbox.targets import (
    Candidate,
    candidates,
    ordered_sample,
    reference_graph,
    sample_targets,
    stratify,
)

MINIPY = Path(__file__).resolve().parents[2] / "fixtures" / "sandbox" / "minipy"
CALC = "mini/calc.py"
TESTS = "tests/test_calc.py"
SYM = "scip-python python minipy 0 "


def _def(line: int, col: int, name: str, symbol: str) -> Occurrence:
    return Occurrence(line, col, line, col + len(name), SYM + symbol, 1)


def _ref(line: int, col: int, name: str, symbol: str) -> Occurrence:
    return Occurrence(line, col, line, col + len(name), SYM + symbol, 0)


def minipy_index() -> ScipIndex:
    """What scip-python reports for the fixture (0-based lines), written by hand."""
    calc = Document(
        relative_path=CALC,
        language="python",
        occurrences=[
            _def(3, 4, "add", "`mini.calc`/add()."),
            Occurrence(4, 4, 4, 9, "local 0", 1),
            _def(8, 4, "double", "`mini.calc`/double()."),
            _ref(9, 13, "add", "`mini.calc`/add()."),
            _def(13, 6, "Counter", "`mini.calc`/Counter#"),
            _def(14, 8, "__init__", "`mini.calc`/Counter#__init__()."),
            _def(17, 8, "bump", "`mini.calc`/Counter#bump()."),
            _ref(18, 21, "add", "`mini.calc`/add()."),
            _def(22, 4, "unused", "`mini.calc`/unused()."),
        ],
    )
    tests = Document(
        relative_path=TESTS,
        language="python",
        occurrences=[
            _ref(2, 22, "Counter", "`mini.calc`/Counter#"),
            _ref(2, 31, "add", "`mini.calc`/add()."),
            _def(5, 4, "test_add", "`tests.test_calc`/test_add()."),
            _ref(6, 11, "add", "`mini.calc`/add()."),
            _def(10, 4, "test_double", "`tests.test_calc`/test_double()."),
            _ref(11, 11, "double", "`mini.calc`/double()."),
            _def(15, 8, "test_bump", "`tests.test_calc`/TestCounter#test_bump()."),
            _ref(16, 18, "Counter", "`mini.calc`/Counter#"),
            _ref(17, 23, "bump", "`mini.calc`/Counter#bump()."),
        ],
    )
    return ScipIndex(
        tool_name="scip-python",
        tool_version="0.6.8",
        project_root="file:///src",
        documents=[calc, tests],
    )


def minipy_parsed() -> dict[str, ParsedFile]:
    return {path: parse_bytes(path, (MINIPY / path).read_bytes()) for path in (CALC, TESTS)}


def test_encode_and_read_round_trip() -> None:
    index = minipy_index()
    decoded = read_index(encode_index(index))
    assert decoded == index
    assert decoded.path_prefix() == ""


def test_path_prefix_for_a_subproject() -> None:
    index = ScipIndex(project_root="file:///src/packages/remeda/")
    assert index.path_prefix() == "packages/remeda/"
    assert ScipIndex(project_root="file:///tmp/work/pkg").path_prefix() == "pkg/"
    assert ScipIndex(project_root="file:///tmp/work").path_prefix() == ""
    assert ScipIndex(project_root="file:///elsewhere").path_prefix() == ""


def test_truncated_index_raises() -> None:
    with pytest.raises(ScipError):
        read_index(encode_index(minipy_index())[:-3])


def test_reference_graph_attributes_references_to_definitions() -> None:
    graph = reference_graph(minipy_index(), minipy_parsed())
    add = "py:mini/calc.py:add:function"
    assert graph.references["py:mini/calc.py:double:function"] == [add]
    assert graph.references["py:mini/calc.py:Counter.bump:method"] == [add]
    assert graph.references["py:tests/test_calc.py:TestCounter.test_bump:test"] == [
        "py:mini/calc.py:Counter.bump:method",
        "py:mini/calc.py:Counter:class",
    ]
    assert "py:mini/calc.py:unused:function" not in graph.from_tests
    assert graph.unmatched_definitions == 0


def test_candidates_are_reached_mutable_functions_with_fan_in() -> None:
    found = candidates(minipy_index(), minipy_parsed())
    assert {c.key: (c.fan_in, c.hops) for c in found} == {
        "py:mini/calc.py:double:function": (1, 1),
        "py:mini/calc.py:Counter.bump:method": (1, 1),
        "py:mini/calc.py:add:function": (3, 1),
    }


def test_hops_follow_references_from_reached_functions() -> None:
    index = minipy_index()
    # Only test_double references library code now: add is reached at hop 2 through double.
    index.documents[1].occurrences = [
        o for o in index.documents[1].occurrences if "double" in o.symbol or o.is_definition
    ]
    found = {c.key: c.hops for c in candidates(index, minipy_parsed())}
    assert found == {"py:mini/calc.py:double:function": 1, "py:mini/calc.py:add:function": 2}


def _fake(n: int) -> list[Candidate]:
    return [Candidate(key=f"k{i:02d}", fan_in=i % 10, hops=1) for i in range(n)]


def test_stratify_splits_into_fan_in_quartiles() -> None:
    groups = stratify(_fake(40))
    assert [len(g) for g in groups] == [10, 10, 10, 10]
    assert max(c.fan_in for c in groups[0]) <= min(c.fan_in for c in groups[1])
    assert {c.stratum for c in groups[3]} == {3}


def test_sampling_is_seeded_stratified_and_prefix_stable() -> None:
    found = _fake(40)
    first = ordered_sample(found, seed=7)
    assert first == ordered_sample(found, seed=7)
    assert first != ordered_sample(found, seed=8)
    assert [c.stratum for c in first[:8]] == [0, 1, 2, 3, 0, 1, 2, 3]
    assert sorted(c.key for c in first) == sorted(c.key for c in found)


def test_sample_targets_returns_n_keys() -> None:
    keys = sample_targets(minipy_index(), minipy_parsed(), n=2, seed=7)
    assert len(keys) == 2
    assert keys == sample_targets(minipy_index(), minipy_parsed(), n=2, seed=7)
