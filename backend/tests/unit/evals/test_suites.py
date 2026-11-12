from __future__ import annotations

import pytest

from repoatlas.evals.registry import get_suite
from repoatlas.evals.suites import SuiteError, benchmark_info, load_benchmark


def test_the_benchmark_suite_loads_the_frozen_splits() -> None:
    suite = get_suite("benchmark")
    dev, test = suite.load("dev"), suite.load("test")
    assert (len(dev), len(test)) == (40, 90)
    assert len(load_benchmark("all")) == 130
    assert {item.split for item in dev} == {"dev"}
    assert not {item.id for item in dev} & {item.id for item in test}
    info = benchmark_info()
    assert info["benchmark_version"] == "1.0.0"
    assert info["test_frozen"] is True
    assert info["test_sha256"] == "163c2957919f00015870f1697e9a140903077d0fb7273beabd2f57767dd4cd08"


def test_every_benchmark_type_has_a_scorer() -> None:
    suite = get_suite("benchmark")
    for item in load_benchmark("all"):
        assert callable(suite.scorer_for(item.type))


def test_unknown_splits_are_refused() -> None:
    with pytest.raises(SuiteError):
        load_benchmark("train")
