"""The registered impact system (T8): the adapter to ImpactPrediction, and a scored run."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoatlas.bench.schema import ImpactTruth, Location, RealFailingTest
from repoatlas.config import Settings
from repoatlas.evals.registry import (
    EvalContext,
    EvalItem,
    ImpactTarget,
    get_impact_system,
    impact_system_names,
    system_kind,
)
from repoatlas.evals.runner import run_eval
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.schema import ImpactParams, SymbolTarget
from repoatlas.impact.system import EngineImpactSystem, impact_engine, to_prediction
from tests.unit.evals.helpers import ScriptedSystem
from tests.unit.evals.test_runner import make_run
from tests.unit.impact.helpers import MAIN, PARSE, TEST_PARSE, TOKENIZE, project_data

pytestmark = pytest.mark.anyio


def system(**options: object) -> EngineImpactSystem:
    return EngineImpactSystem(ImpactEngine(project_data()), **options)  # type: ignore[arg-type]


async def test_predictions_keep_the_ranking_and_only_symbol_keys() -> None:
    prediction = await system().predict(1, ImpactTarget(symbol_key=PARSE))
    keys = [item.symbol_key for item in prediction.items]
    assert MAIN in keys and TEST_PARSE in keys
    assert not any(key.startswith(("file:", "dir:", "route:")) for key in keys)
    assert [i.score for i in prediction.items] == sorted(
        (i.score for i in prediction.items), reverse=True
    )
    test = next(i for i in prediction.items if i.symbol_key == TEST_PARSE)
    assert test.group == "test"
    assert test.path is not None and test.path.startswith(PARSE)
    assert test.evidence and test.evidence[0].path == "tests/test_core.py"


async def test_unknown_sites_join_the_prediction_only_when_asked() -> None:
    engine = ImpactEngine(project_data())
    report = engine.analyze(1, SymbolTarget(symbol_key=TOKENIZE))
    target = ImpactTarget(symbol_key=TOKENIZE)
    assert all(i.group != "unknown" for i in to_prediction(report, target).items)
    widened = to_prediction(report, target, include_unknown=True)
    unknown = [i for i in widened.items if i.group == "unknown"]
    # main's unresolved `obj._tokenize()` is already a ranked item (through parse); the
    # string literal is module-level code (a file), so nothing new is added here
    assert all(i.score == 0.0 for i in unknown)
    assert len({i.symbol_key for i in widened.items}) == len(widened.items)


def test_the_config_names_the_parameters() -> None:
    default = system().config()
    tuned = system(params=ImpactParams(max_depth=2)).config()
    assert default["params"]["max_depth"] == 4 and tuned["params"]["max_depth"] == 2
    assert default != tuned


def test_it_is_registered_and_needs_no_model() -> None:
    assert "impact_engine" in impact_system_names()
    assert system_kind("impact_engine") == "impact"
    assert get_impact_system("impact_engine") is impact_engine
    built = impact_engine(
        EvalContext(
            settings=Settings(),
            store=object(),  # type: ignore[arg-type]
            options={"params": {"decay": 0.5}, "include_unknown": True},
        )
    )
    assert built.params.decay == 0.5 and built.include_unknown


async def test_a_scored_run_against_static_and_real_ground_truth(tmp_path: Path) -> None:
    def loc(key: str) -> Location:
        return Location(symbol_key=key, path=key.split(":")[1], line=1)

    item = EvalItem(
        id="p-impact-001",
        type="impact",
        text="What breaks if I change `_tokenize` (defined in `pkg/core.py`)?",
        repo_id="r",
        commit="c" * 40,
        split="dev",
        truth=ImpactTruth(
            target=loc(TOKENIZE),
            static=[loc(PARSE), loc(MAIN), loc(TEST_PARSE)],
            real_failing_tests=[
                RealFailingTest(test_id="tests/test_core.py::test_parse", symbol_key=TEST_PARSE)
            ],
        ),
    )
    run = make_run(tmp_path, ScriptedSystem({}), kind="impact", items=[item])
    run.system = system()
    run.snapshot_for = lambda _item: 1
    result = await run_eval(run)
    (record,) = result.records
    assert record.score.primary == pytest.approx(1.0)
    assert record.score.metrics["real_recall"] == pytest.approx(1.0)
    assert record.score.metrics["real_recall_at_10"] == pytest.approx(1.0)
