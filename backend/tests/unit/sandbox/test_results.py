from __future__ import annotations

from pathlib import Path

import pytest

from repoatlas.sandbox.parsed import parse_bytes
from repoatlas.sandbox.results import (
    ReportError,
    TestIndex,
    TestResult,
    map_test_id,
    normalize_test_id,
    parse_jest_json,
    parse_junit,
    parse_report,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sandbox"
REPORTS = FIXTURES / "reports"


def _outcomes(results: list[TestResult]) -> dict[tuple[str, bool], str]:
    return {(r.test_id, r.collection): r.outcome for r in results}


def test_junit_xunit1() -> None:
    results = parse_junit((REPORTS / "junit_xunit1.xml").read_bytes())
    assert _outcomes(results) == {
        ("tests/test_bad.py", True): "error",
        ("tests/test_calc.py::TestCounter::test_bump", False): "error",
        ("tests/test_calc.py::test_add", False): "passed",
        ("tests/test_calc.py::test_double[1]", False): "failed",
        ("tests/test_calc.py::test_double[2]", False): "passed",
        ("tests/test_calc.py::test_skipped", False): "skipped",
    }
    assert [r.test_id for r in results] == sorted(r.test_id for r in results)


def test_junit_xunit2_uses_known_paths() -> None:
    data = (REPORTS / "junit_xunit2.xml").read_bytes()
    results = parse_junit(data, known_paths=["tests/test_calc.py", "tests/__init__.py"])
    assert _outcomes(results) == {
        ("tests/test_calc.py::TestCounter::test_bump", False): "failed",
        ("tests/test_calc.py::test_add", False): "passed",
    }
    # Without known paths the whole classname is taken as the module.
    unknown = parse_junit(data)
    assert "tests/test_calc/TestCounter.py::test_bump" in {r.test_id for r in unknown}


def test_vitest_json() -> None:
    results = parse_jest_json((REPORTS / "vitest.json").read_bytes())
    assert _outcomes(results) == {
        ("src/math.test.ts > math > add > adds numbers", False): "passed",
        ("src/math.test.ts > math > doubles 3", False): "failed",
        ("src/math.test.ts > later", False): "skipped",
        ("src/broken.test.ts", True): "error",
    }
    durations = {r.test_id: r.duration for r in results}
    assert durations["src/math.test.ts > math > add > adds numbers"] == 0.0025


def test_duplicate_rows_keep_the_worst_outcome() -> None:
    xml = (
        b'<testsuite><testcase classname="t" name="a" file="t.py"/>'
        b'<testcase classname="t" name="a" file="t.py"><failure/></testcase></testsuite>'
    )
    assert _outcomes(parse_junit(xml)) == {("t.py::a", False): "failed"}


def test_bad_reports_raise() -> None:
    with pytest.raises(ReportError):
        parse_junit(b"<not xml")
    with pytest.raises(ReportError):
        parse_jest_json(b"{")
    with pytest.raises(ValueError, match="Unknown reporter"):
        parse_report("tap", b"")


def test_normalize_strips_pytest_params_only() -> None:
    assert normalize_test_id("a.py::T::test_x[1-a::b]") == "a.py::T::test_x"
    assert normalize_test_id("a.ts > works [fast]") == "a.ts > works [fast]"


PY_TESTS = (FIXTURES / "minipy" / "tests" / "test_calc.py").read_text()

TS_TESTS = """\
import { describe, it, test } from "vitest";
describe("math", () => {
  describe("add", () => {
    it("adds numbers", () => {});
  });
  it.each([1, 2])("doubles %i", (n) => {});
});
test("top level", () => {});
"""


def test_map_pytest_ids_to_test_keys() -> None:
    parsed = [parse_bytes("tests/test_calc.py", PY_TESTS.encode())]
    index = TestIndex(parsed)
    assert index.map("tests/test_calc.py::test_add") == "py:tests/test_calc.py:test_add:test"
    assert index.map("tests/test_calc.py::test_double[2]") == (
        "py:tests/test_calc.py:test_double:test"
    )
    assert index.map("tests/test_calc.py::TestCounter::test_bump") == (
        "py:tests/test_calc.py:TestCounter.test_bump:test"
    )
    assert index.map("tests/test_calc.py::test_missing") is None
    assert map_test_id("tests/test_calc.py::test_add", parsed) == index.map(
        "tests/test_calc.py::test_add"
    )


def test_map_vitest_ids_to_test_keys() -> None:
    parsed = {"src/math.test.ts": parse_bytes("src/math.test.ts", TS_TESTS.encode())}
    index = TestIndex(parsed)
    nested = index.map("src/math.test.ts > math > add > adds numbers")
    assert nested is not None and nested.startswith("ts:src/math.test.ts:")
    # parse_file does not extract it.each rows, so they stay unmapped (and are counted).
    assert index.map("src/math.test.ts > math > doubles 2") is None
    assert index.map("src/math.test.ts > top level") is not None
    assert index.map("src/math.test.ts > math > triples 2") is None
