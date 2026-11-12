"""SBX-6 to SBX-10 end to end on the fixture project: index, sample, mutate, confirm."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoatlas.ingest.source import DirectorySourceTree
from repoatlas.sandbox.baseline import run_baseline
from repoatlas.sandbox.mutations import read_records, run_mutations
from repoatlas.sandbox.parsed import parse_tree
from repoatlas.sandbox.runner import SandboxRunner
from repoatlas.sandbox.scip import ScipIndex, read_index
from repoatlas.sandbox.targets import candidates, ordered_sample
from tests.integration.sandbox.fixtures import FIXTURES, minipy_image, minipy_recipe


def _runner() -> SandboxRunner:
    return SandboxRunner(minipy_recipe(), minipy_image().image)


@pytest.fixture(scope="module")
def index() -> ScipIndex:
    artifact, data = _runner().run_scip()
    assert artifact.indexer == "scip-python"
    assert artifact.version == "0.6.8"
    assert len(artifact.sha256) == 64
    return read_index(data)


def test_scip_index_covers_the_fixture(index: ScipIndex) -> None:
    paths = {document.relative_path for document in index.documents}
    assert {"mini/calc.py", "tests/test_calc.py"} <= paths
    assert index.tool_name == "scip-python"
    assert index.path_prefix() == ""


def test_scip_targets_are_the_reached_functions(index: ScipIndex) -> None:
    parsed = parse_tree(DirectorySourceTree(FIXTURES / "minipy"))
    found = {c.key: c.hops for c in candidates(index, parsed)}
    assert found == {
        "py:mini/calc.py:add:function": 1,
        "py:mini/calc.py:double:function": 1,
        "py:mini/calc.py:Counter.bump:method": 1,
    }


def test_mutation_run_confirms_failures_and_resumes(index: ScipIndex, tmp_path: Path) -> None:
    runner = _runner()
    tree = DirectorySourceTree(FIXTURES / "minipy")
    parsed = parse_tree(tree)
    baseline = run_baseline(runner, image_digest=minipy_image().image.digest, runs=2)
    assert baseline.pre_failing == []
    targets = ordered_sample(candidates(index, parsed), seed=7)
    out = tmp_path / "mutations.jsonl"

    first = run_mutations(
        runner, targets[:1], tree=tree, parsed=parsed, baseline=baseline, seed=7, out=out
    )
    assert len(first) == 1
    records = run_mutations(
        runner, targets, tree=tree, parsed=parsed, baseline=baseline, seed=7, out=out
    )
    assert [r.symbol_key for r in records] == [t.key for t in targets]  # the first was kept
    assert read_records(out) == records

    by_key = {record.symbol_key: record for record in records}
    double = by_key["py:mini/calc.py:double:function"]
    assert double.status == "confirmed"
    assert [(t.test_id, t.symbol_key) for t in double.failing] == [
        ("tests/test_calc.py::test_double[1]", "py:tests/test_calc.py:test_double:test"),
        ("tests/test_calc.py::test_double[2]", "py:tests/test_calc.py:test_double:test"),
    ]
    add = by_key["py:mini/calc.py:add:function"]
    assert {t.test_id for t in add.failing} == {
        "tests/test_calc.py::TestCounter::test_bump",
        "tests/test_calc.py::test_add",
        "tests/test_calc.py::test_double[1]",
        "tests/test_calc.py::test_double[2]",
    }
    assert all(record.runtime_s > 0 for record in records)
