"""SandboxRunner.run_tests on the fixture project: full runs, selected runs, mutated runs."""

from __future__ import annotations

from repoatlas.sandbox.runner import Mutation, SandboxRunner
from tests.integration.sandbox.fixtures import FIXTURES, minipy_image, minipy_recipe


def _runner() -> SandboxRunner:
    return SandboxRunner(minipy_recipe(), minipy_image().image)


def test_full_run_parses_the_report() -> None:
    run = _runner().run_tests()
    assert run.report_error is None
    assert run.exit_code == 0
    assert run.outcomes() == {
        "tests/test_calc.py::TestCounter::test_bump": "passed",
        "tests/test_calc.py::test_add": "passed",
        "tests/test_calc.py::test_double[1]": "passed",
        "tests/test_calc.py::test_double[2]": "passed",
        "tests/test_calc.py::test_skipped": "skipped",
    }


def test_selected_run_runs_only_those_tests() -> None:
    run = _runner().run_tests(only=["tests/test_calc.py::test_add"])
    assert run.outcomes() == {"tests/test_calc.py::test_add": "passed"}


def test_mutated_run_sees_the_changed_source() -> None:
    source = (FIXTURES / "minipy" / "mini" / "calc.py").read_text()
    mutated = source.replace(
        "    result = add(a, a)\n    return result", "    raise RuntimeError('x')"
    )
    mutation = Mutation(
        symbol_key="py:mini/calc.py:double:function",
        path="mini/calc.py",
        kind="raise",
        content=mutated.encode(),
    )
    runner = _runner()
    run = runner.run_tests(mutation=mutation)
    failed = {test_id for test_id, outcome in run.outcomes().items() if outcome == "failed"}
    assert failed == {"tests/test_calc.py::test_double[1]", "tests/test_calc.py::test_double[2]"}
    # The mutated layer is removed and the repo image is untouched.
    assert runner.run_tests().exit_code == 0
