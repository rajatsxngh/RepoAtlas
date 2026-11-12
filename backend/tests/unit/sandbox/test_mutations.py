from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from repoatlas.sandbox.baseline import Baseline
from repoatlas.sandbox.mutations import mutate_target, read_records, run_mutations
from repoatlas.sandbox.parsed import parse_bytes
from repoatlas.sandbox.results import TestIndex, TestResult
from repoatlas.sandbox.runner import Mutation, SandboxRunner, TestRun
from repoatlas.sandbox.targets import Candidate

SOURCE = b"def f(x):\n    y = x + 1\n    return y\n\n\ndef g(x): return x\n"
TESTS = b"def test_a():\n    pass\n\n\ndef test_b():\n    pass\n"


def _run(outcomes: dict[str, str], *, collection: Sequence[str] = ()) -> TestRun:
    results = [TestResult(test_id=k, outcome=v) for k, v in outcomes.items()]  # type: ignore[arg-type]
    results += [TestResult(test_id=c, outcome="error", collection=True) for c in collection]
    return TestRun(results=results, duration_s=1.0, exit_code=1, log_tail="")


class FakeRunner:
    def __init__(self, full: TestRun, confirm: TestRun | None) -> None:
        self.full, self.confirm = full, confirm
        self.selected: list[str] = []
        self.mutations: list[Mutation] = []

    def run_confirmed(
        self, mutation: Mutation, *, failing: Callable[[TestRun], Sequence[str]]
    ) -> tuple[TestRun, TestRun | None]:
        self.mutations.append(mutation)
        self.selected = list(failing(self.full))
        return self.full, self.confirm if self.selected else None


def _baseline() -> Baseline:
    return Baseline(
        repo_id="r",
        commit="c" * 40,
        image_digest="sha256:x",
        runs=[],
        outcomes={},
        flaky={"tests/test_m.py::test_flaky": ["passed", "failed"]},
        quarantined=["tests/test_m.py::test_flaky"],
        pre_failing=["tests/test_m.py::test_pre"],
        collection_errors=[],
    )


TEST_INDEX = TestIndex([parse_bytes("tests/test_m.py", TESTS)])
F = Candidate(key="py:m.py:f:function", fan_in=2, hops=1, stratum=1)


def _mutate(runner: FakeRunner, candidate: Candidate = F) -> Any:
    return mutate_target(
        cast(SandboxRunner, runner),
        candidate,
        source=SOURCE,
        baseline=_baseline(),
        tests=TEST_INDEX,
        seed=7,
    )


def test_confirmed_failures_exclude_quarantined_pre_failing_and_flaky_reruns() -> None:
    full = _run(
        {
            "tests/test_m.py::test_a": "failed",
            "tests/test_m.py::test_b[1]": "error",
            "tests/test_m.py::test_c": "failed",  # passes on the re-run
            "tests/test_m.py::test_flaky": "failed",
            "tests/test_m.py::test_pre": "failed",
            "tests/test_m.py::test_ok": "passed",
        },
        collection=["tests/test_other.py"],
    )
    confirm = _run(
        {
            "tests/test_m.py::test_a": "failed",
            "tests/test_m.py::test_b[1]": "error",
            "tests/test_m.py::test_c": "passed",
        }
    )
    runner = FakeRunner(full, confirm)
    record = _mutate(runner)
    assert runner.selected == [
        "tests/test_m.py::test_a",
        "tests/test_m.py::test_b[1]",
        "tests/test_m.py::test_c",
    ]
    assert b'raise RuntimeError("repoatlas-mutation")' in runner.mutations[0].content
    assert record.status == "confirmed"
    assert [(t.test_id, t.symbol_key) for t in record.failing] == [
        ("tests/test_m.py::test_a", "py:tests/test_m.py:test_a:test"),
        ("tests/test_m.py::test_b[1]", "py:tests/test_m.py:test_b:test"),
    ]
    assert record.unconfirmed == ["tests/test_m.py::test_c"]
    assert record.excluded == {"quarantined": 1, "pre_failing": 1, "collection": 1}
    assert (record.seed, record.stratum, record.fan_in) == (7, 1, 2)


def test_no_failures_is_recorded_without_a_rerun() -> None:
    runner = FakeRunner(_run({"tests/test_m.py::test_a": "passed"}), None)
    record = _mutate(runner)
    assert record.status == "no_failures" and record.failing == []


def test_suite_errors_and_skips_are_recorded() -> None:
    broken = TestRun(results=[], duration_s=1.0, exit_code=2, log_tail="", report_error="no report")
    assert _mutate(FakeRunner(broken, None)).status == "suite_error"
    skipped = _mutate(
        FakeRunner(broken, None), Candidate(key="py:m.py:g:function", fan_in=0, hops=1)
    )
    assert skipped.status == "skipped" and skipped.detail == "under 2 lines"


class FakeTree:
    label = "fake"
    commit = None

    def entries(self) -> list[Any]:
        from repoatlas.ingest.source import TreeEntry

        return [TreeEntry(path="m.py", mode="100644", oid="0" * 40, size=len(SOURCE))]

    def read(self, entry: Any) -> bytes:
        return SOURCE


def test_run_mutations_appends_and_resumes(tmp_path: Path) -> None:
    out = tmp_path / "mutations.jsonl"
    full = _run({"tests/test_m.py::test_a": "failed"})
    runner = FakeRunner(full, full)
    targets = [F, Candidate(key="py:m.py:g:function", fan_in=0, hops=1)]
    kwargs: dict[str, Any] = {
        "tree": FakeTree(),
        "parsed": {"tests/test_m.py": parse_bytes("tests/test_m.py", TESTS)},
        "baseline": _baseline(),
        "seed": 7,
        "out": out,
    }
    run_mutations(cast(SandboxRunner, runner), targets[:1], **kwargs)
    records = run_mutations(cast(SandboxRunner, runner), targets, **kwargs)
    assert [r.symbol_key for r in records] == [F.key, "py:m.py:g:function"]
    assert len(runner.mutations) == 1  # f was not mutated again
    assert read_records(out) == records
    assert out.read_text().count("\n") == 2
