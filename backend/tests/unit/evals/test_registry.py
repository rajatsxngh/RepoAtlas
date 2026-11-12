from __future__ import annotations

import pytest
from pydantic import ValidationError

from repoatlas.config import Settings
from repoatlas.evals import registry
from repoatlas.evals.outputs import ItemScore, SystemOutput
from repoatlas.evals.registry import (
    EvalContext,
    EvalItem,
    ImpactItem,
    ImpactPrediction,
    ImpactTarget,
    RegistryError,
)


class _Echo:
    name = "echo"

    async def answer(self, snapshot_id, question, budget, *, on_status=None):
        raise NotImplementedError


class _Impact:
    name = "fake_impact"

    async def predict(self, snapshot_id: int, target: ImpactTarget) -> ImpactPrediction:
        return ImpactPrediction(target=target, items=[])


def test_answer_systems_register_and_build_by_name() -> None:
    registry.register_answer_system("test_echo", lambda ctx: _Echo(), replace=True)
    factory = registry.get_answer_system("test_echo")
    system = factory(EvalContext(settings=Settings()))
    assert system.name == "echo"
    assert "test_echo" in registry.answer_system_names()
    assert registry.system_kind("test_echo") == "answer"


def test_registering_a_name_twice_needs_replace() -> None:
    registry.register_answer_system("test_twice", lambda ctx: _Echo(), replace=True)
    with pytest.raises(RegistryError, match="already registered"):
        registry.register_answer_system("test_twice", lambda ctx: _Echo())


def test_unknown_names_list_what_is_registered() -> None:
    with pytest.raises(RegistryError, match="No answer system named 'nope'"):
        registry.get_answer_system("nope")
    with pytest.raises(RegistryError, match="No system named"):
        registry.system_kind("nope")
    with pytest.raises(RegistryError, match="No suite named"):
        registry.get_suite("nope")


def test_impact_systems_register_by_name() -> None:
    registry.register_impact_system("test_impact", lambda ctx: _Impact(), replace=True)
    assert registry.system_kind("test_impact") == "impact"
    assert "test_impact" in registry.impact_system_names()
    with pytest.raises(RegistryError, match="already registered"):
        registry.register_impact_system("test_impact", lambda ctx: _Impact())


def test_impact_target_is_a_symbol_or_a_diff() -> None:
    assert ImpactTarget(symbol_key="py:a.py:f:function").diff is None
    assert ImpactTarget(diff="--- a\n+++ b\n").symbol_key is None
    with pytest.raises(ValidationError):
        ImpactTarget()
    with pytest.raises(ValidationError):
        ImpactTarget(symbol_key="k", diff="d")


def test_impact_prediction_ranks_by_score_then_order() -> None:
    target = ImpactTarget(symbol_key="t")
    items = [
        ImpactItem(symbol_key="a", group="caller", score=0.5),
        ImpactItem(symbol_key="b", group="test", score=0.9),
        ImpactItem(symbol_key="c", group="unknown", score=0.5),
    ]
    ranked = ImpactPrediction(target=target, items=items).ranked()
    assert [item.symbol_key for item in ranked] == ["b", "a", "c"]
    with pytest.raises(ValidationError):
        ImpactItem(symbol_key="x", group="other", score=1.0)  # type: ignore[arg-type]


def _score(item: EvalItem, output: SystemOutput) -> ItemScore:
    return ItemScore(question_id=item.id, type=item.type, outcome=output.outcome, primary=1.0)


def test_suites_have_their_own_scorers() -> None:
    items = [EvalItem(id="q1", type="lookup", text="?", repo_id="r", commit="c", split="dev")]
    suite = registry.register_suite(
        "test_suite",
        lambda split: [i for i in items if i.split == split],
        {"lookup": _score},
        replace=True,
    )
    assert registry.get_suite("test_suite") is suite
    assert [item.id for item in suite.load("dev")] == ["q1"]
    assert suite.load("test") == []
    assert suite.scorer_for("lookup") is _score
    with pytest.raises(RegistryError, match="no scorer for 'flow'"):
        suite.scorer_for("flow")
    fallback = registry.register_suite("test_any", lambda split: [], {"*": _score}, replace=True)
    assert fallback.scorer_for("flow") is _score
    with pytest.raises(ValueError, match="at least one scorer"):
        registry.register_suite("test_empty", lambda split: [], {}, replace=True)
