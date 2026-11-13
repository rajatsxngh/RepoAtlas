"""The parameter sweep (T9): grid scoring with the benchmark scorer, ranking, dev only."""

from __future__ import annotations

import pytest

from repoatlas.bench.schema import ImpactTruth, Location, RealFailingTest
from repoatlas.evals.registry import EvalItem
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.schema import ImpactParams
from repoatlas.impact.tuning import Grid, score_point, sweep, sweep_markdown
from tests.unit.impact.helpers import PARSE, TEST_PARSE, TOKENIZE, project_data


def loc(key: str) -> Location:
    return Location(symbol_key=key, path=key.split(":")[1], line=1)


ITEM = EvalItem(
    id="p-impact-001",
    type="impact",
    text="What breaks if I change `_tokenize` (defined in `pkg/core.py`)?",
    repo_id="p",
    commit="c" * 40,
    split="dev",
    truth=ImpactTruth(
        target=loc(TOKENIZE),
        static=[loc(PARSE)],  # depth 1 only
        real_failing_tests=[RealFailingTest(test_id="t", symbol_key=TEST_PARSE)],
    ),
)


def engine() -> ImpactEngine:
    return ImpactEngine(project_data())


def test_a_point_is_scored_like_the_runner_scores() -> None:
    deep = score_point(engine(), [ITEM], lambda _i: 1, ImpactParams(max_depth=2))
    (q,) = deep.questions
    assert q.static_precision == pytest.approx(1 / 3)  # parse, main, test_parse predicted
    assert q.static_recall == 1.0
    assert q.real_f1 == 1.0
    assert deep.objective == pytest.approx((0.5 + 1.0) / 2)
    shallow = score_point(engine(), [ITEM], lambda _i: 1, ImpactParams(max_depth=1))
    assert shallow.static_f1 == 1.0 and shallow.real_f1 == 0.0


def test_the_sweep_ranks_points_and_breaks_ties_towards_smaller_searches() -> None:
    result = sweep(
        engine(),
        [ITEM, MAIN_ITEM],
        lambda _i: 1,
        grid=Grid(depths=(1, 2, 3), decays=(0.5, 0.9), min_scores=(0.05, 0.3)),
    )
    assert len(result.points) == 12
    objectives = [p.objective for p in result.points]
    assert objectives == sorted(objectives, reverse=True)
    best = result.best
    tied = [p for p in result.points if p.objective == best.objective]
    assert best.max_depth == min(p.max_depth for p in tied)
    assert result.questions == ["p-impact-001"]  # non-impact items are ignored
    assert best.params().max_depth == best.max_depth
    text = sweep_markdown(result)
    assert "| default |" in text and "p-impact-001" in text


def test_only_dev_is_tuned_on() -> None:
    with pytest.raises(ValueError):
        sweep(engine(), [ITEM], lambda _i: 1, split="test")


MAIN_ITEM = EvalItem(
    id="p-lookup-001",
    type="lookup",
    text="Where is main defined?",
    repo_id="p",
    commit="c" * 40,
    split="dev",
    truth=None,
)
