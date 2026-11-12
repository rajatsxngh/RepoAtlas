from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repoatlas.answers.schema import Budget
from repoatlas.bench.schema import ImpactTruth, Location
from repoatlas.evals.mapping import answer_output
from repoatlas.evals.outputs import SupportLabel, SystemOutput
from repoatlas.evals.registry import EvalItem, ImpactItem, ImpactPrediction, ImpactTarget
from repoatlas.evals.runner import (
    CostMeter,
    Enricher,
    EvalRun,
    ResultCache,
    RunRecord,
    Skipped,
    run_eval,
    system_fingerprint,
)
from tests.unit.evals.helpers import (
    CLS,
    INDEX,
    METH,
    OTHER_F,
    SUITE,
    F,
    G,
    ScriptedSystem,
    answer,
    items,
    ref,
)

pytestmark = pytest.mark.anyio

LOOKUP, CALLERS, UNANS = (item.text for item in items())


def script(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        LOOKUP: answer([("c1", "f", [ref("src/m.py", 10, 20, F)])]),
        CALLERS: answer(
            [("c1", "g", [ref("src/m.py", 22, 30)]), ("c2", "run", [ref("src/m.py", 45, 60)])]
        ),
        UNANS: answer([], refused=True, kind="evidence", cost=0.0),
    }
    base.update(overrides)
    return base


def make_run(tmp_path: Path, system: ScriptedSystem, **kwargs: Any) -> EvalRun:
    options: dict[str, Any] = {
        "system_name": "scripted",
        "system": system,
        "kind": "answer",
        "suite": SUITE,
        "split": "dev",
        "items": items(),
        "runs": 1,
        "snapshot_for": lambda item: 7,
        "index_for": lambda snapshot_id: INDEX,
        "cache": ResultCache(tmp_path / "cache"),
        "fingerprint": system_fingerprint("scripted", system.config(), {"r": "fp"}, Budget()),
    }
    options.update(kwargs)
    return EvalRun(**options)


# --- mapping ---------------------------------------------------------------------------


def test_citations_map_to_their_symbol_or_the_innermost_containing_one() -> None:
    assert INDEX.key_for_ref(ref("src/m.py", 50, 52)) == METH
    assert INDEX.key_for_ref(ref("src/m.py", 41, 42)) == CLS
    assert INDEX.key_for_ref(ref("src/m.py", 1, 3)) == "file:src/m.py"
    assert INDEX.key_for_ref(ref("src/m.py", 1, 3, G)) == G
    assert INDEX.key_for_ref(ref("src/none.py", 1, 3)) is None


def test_claim_keys_match_the_tag_against_cited_symbols() -> None:
    assert INDEX.claim_keys("g", [ref("src/m.py", 1, 40)]) == [G]  # named in the range
    assert INDEX.claim_keys("K.run()", [ref("src/m.py", 50, 52)]) == [METH]  # containing symbol
    assert INDEX.claim_keys("K", [ref("src/m.py", 50, 52)]) == [CLS]  # a parent
    assert INDEX.claim_keys("nothing", [ref("src/m.py", 10, 20)]) == []
    assert INDEX.claim_keys(None, [ref("src/m.py", 10, 20)]) == []


def test_without_citations_tags_resolve_by_name_and_mentioned_path() -> None:
    assert INDEX.claim_keys("g", []) == [G]
    assert INDEX.claim_keys("f", []) == []  # two symbols named f
    assert INDEX.claim_keys("f", [], "f in src/o.py") == [OTHER_F]


def test_answer_output_keeps_metrics_and_deduplicates_citations() -> None:
    duplicate = ref("src/m.py", 10, 20, F)
    result = answer([("c1", "f", [duplicate, duplicate]), ("c2", None, [ref("src/m.py", 22, 22)])])
    output = answer_output(result, INDEX, 1.23456)
    assert output.citations == [duplicate, ref("src/m.py", 22, 22)]
    assert output.cited_keys == [F, G]
    assert output.answer_keys == [F]
    assert output.latency_s == 1.235
    assert output.prefilter_citation_validity == 1.0
    refused = answer_output(answer([], refused=True, kind="api"), INDEX, 0.0)
    assert refused.outcome == "refused_api" and refused.refused


# --- runner ------------------------------------------------------------------------------


async def test_a_run_scores_every_question_and_caches_the_outputs(tmp_path: Path) -> None:
    system = ScriptedSystem(script())
    result = await run_eval(make_run(tmp_path, system, runs=2))
    assert [(r.run, r.question_id) for r in result.records] == [
        (0, "r-callers-001"),
        (0, "r-lookup-001"),
        (0, "r-unanswerable-001"),
        (1, "r-callers-001"),
        (1, "r-lookup-001"),
        (1, "r-unanswerable-001"),
    ]
    scores = {r.question_id: r.score.primary for r in result.records if r.run == 0}
    # callers: g is right; run is the question's own target and is not counted
    assert scores == {"r-lookup-001": 1.0, "r-callers-001": 1.0, "r-unanswerable-001": 1.0}
    assert result.complete and result.spent_usd == pytest.approx(0.04)
    assert len(system.calls) == 6

    again = ScriptedSystem(script())
    cached = await run_eval(make_run(tmp_path, again, runs=2))
    assert again.calls == []
    assert cached.spent_usd == 0.0
    assert all(r.cached for r in cached.records)
    assert cached.total_cost_usd == pytest.approx(0.04)  # what the cached answers cost when run


async def test_a_new_fingerprint_does_not_reuse_answers(tmp_path: Path) -> None:
    await run_eval(make_run(tmp_path, ScriptedSystem(script())))
    other = ScriptedSystem(script())
    fingerprint = system_fingerprint("scripted", {"mode": "other"}, {"r": "fp"}, Budget())
    await run_eval(make_run(tmp_path, other, fingerprint=fingerprint))
    assert len(other.calls) == 3


async def test_the_cost_cap_stops_new_questions_and_keeps_partial_results(tmp_path: Path) -> None:
    expensive = {
        text: answer([("c1", "f", [ref("src/m.py", 10, 20)])], cost=0.10)
        for text in (LOOKUP, CALLERS, UNANS)
    }
    system = ScriptedSystem(expensive)
    seen: list[RunRecord | Skipped] = []
    run = make_run(
        tmp_path,
        system,
        runs=2,
        max_cost_usd=0.35,
        concurrency=1,
        budget=Budget(max_cost_usd=0.10),
        progress=seen.append,
    )
    result = await run_eval(run)
    assert len(system.calls) == 3
    assert result.spent_usd == pytest.approx(0.30)
    assert not result.complete
    assert [(s.run, s.question_id) for s in result.skipped] == [
        (1, "r-callers-001"),
        (1, "r-lookup-001"),
        (1, "r-unanswerable-001"),
    ]
    assert len(seen) == 6
    # The same command with a higher cap picks up the skipped items only.
    more = ScriptedSystem(expensive)
    resumed = await run_eval(make_run(tmp_path, more, runs=2, max_cost_usd=1.0, concurrency=1))
    assert len(more.calls) == 3 and resumed.complete


async def test_concurrency_is_bounded(tmp_path: Path) -> None:
    system = ScriptedSystem(script(), delay=0.02)
    await run_eval(make_run(tmp_path, system, runs=3, concurrency=2))
    assert system.max_active == 2
    single = ScriptedSystem(script(), delay=0.01)
    await run_eval(make_run(tmp_path / "b", single, runs=2, concurrency=1))
    assert single.max_active == 1


async def test_errors_are_recorded_cached_and_retried_on_resume(tmp_path: Path) -> None:
    failing = ScriptedSystem(script(**{LOOKUP: RuntimeError("model unavailable")}))
    result = await run_eval(make_run(tmp_path, failing))
    lookup = next(r for r in result.records if r.question_id == "r-lookup-001")
    assert lookup.output.outcome == "error" and "model unavailable" in (lookup.output.error or "")
    assert lookup.score.primary is None
    assert not result.complete

    again = ScriptedSystem(script(**{LOOKUP: RuntimeError("still failing")}))
    await run_eval(make_run(tmp_path, again))
    assert again.calls == []  # the error is cached

    fixed = ScriptedSystem(script())
    resumed = await run_eval(make_run(tmp_path, fixed, resume=True))
    assert fixed.calls == [LOOKUP]
    assert resumed.complete


async def test_a_missing_snapshot_is_an_error_not_a_crash(tmp_path: Path) -> None:
    system = ScriptedSystem(script())
    result = await run_eval(make_run(tmp_path, system, snapshot_for=lambda item: None))
    assert {r.output.outcome for r in result.records} == {"error"}
    assert system.calls == []


def _support_enricher(calls: list[str], *, fail: bool = False) -> Enricher:
    async def run(item: EvalItem, output: SystemOutput, snapshot_id: int | None) -> SystemOutput:
        calls.append(item.id)
        if fail:
            raise RuntimeError("checker down")
        labels = [SupportLabel(claim_id=c.id, label="supported") for c in output.claims]
        return output.model_copy(
            update={"support": labels, "eval_cost_usd": output.eval_cost_usd + 0.005}
        )

    return Enricher(
        name="support",
        fingerprint="chk1",
        applies=lambda item, output: not output.refused,
        run=run,
        store=lambda output: [label.model_dump() for label in output.support or []],
        load=lambda output, data: output.model_copy(
            update={"support": [SupportLabel.model_validate(d) for d in data]}
        ),
    )


async def test_enrichers_are_cached_on_their_own_fingerprint(tmp_path: Path) -> None:
    calls: list[str] = []
    result = await run_eval(
        make_run(tmp_path, ScriptedSystem(script()), enrichers=[_support_enricher(calls)])
    )
    assert sorted(calls) == ["r-callers-001", "r-lookup-001"]  # not the refusal
    lookup = next(r for r in result.records if r.question_id == "r-lookup-001")
    assert lookup.output.support == [SupportLabel(claim_id="c1", label="supported")]
    assert result.spent_usd == pytest.approx(0.03)

    again: list[str] = []
    system = ScriptedSystem(script())
    cached = await run_eval(make_run(tmp_path, system, enrichers=[_support_enricher(again)]))
    assert again == [] and system.calls == []
    assert next(
        r for r in cached.records if r.question_id == "r-lookup-001"
    ).output.eval_cost_usd == pytest.approx(0.005)

    new_checker = _support_enricher(again)
    new_checker.fingerprint = "chk2"
    system = ScriptedSystem(script())
    await run_eval(make_run(tmp_path, system, enrichers=[new_checker]))
    assert system.calls == []  # answers reused
    assert sorted(again) == ["r-callers-001", "r-lookup-001"]


async def test_a_failing_enricher_leaves_a_note(tmp_path: Path) -> None:
    calls: list[str] = []
    result = await run_eval(
        make_run(
            tmp_path, ScriptedSystem(script()), enrichers=[_support_enricher(calls, fail=True)]
        )
    )
    lookup = next(r for r in result.records if r.question_id == "r-lookup-001")
    assert lookup.output.support is None
    assert lookup.output.notes == ["support failed: RuntimeError: checker down"]
    assert lookup.score.primary == 1.0


class _Impact:
    name = "impact"

    def __init__(self) -> None:
        self.targets: list[ImpactTarget] = []

    async def predict(self, snapshot_id: int, target: ImpactTarget) -> ImpactPrediction:
        self.targets.append(target)
        items = [
            ImpactItem(symbol_key="py:src/x.py:x:function", group="caller", score=0.5),
            ImpactItem(symbol_key=G, group="caller", score=0.9),
        ]
        return ImpactPrediction(target=target, items=items)


async def test_impact_systems_answer_only_impact_questions(tmp_path: Path) -> None:
    impact = EvalItem(
        id="r-impact-001",
        type="impact",
        text="What breaks if I change `f` (defined in `src/m.py`)?",
        repo_id="r",
        commit="c" * 40,
        split="dev",
        truth=ImpactTruth(
            target=Location(symbol_key=F, path="src/m.py", line=10),
            static=[Location(symbol_key=G, path="src/m.py", line=22)],
            real_failing_tests=None,
        ),
    )
    system = _Impact()
    run = make_run(tmp_path, ScriptedSystem({}), kind="impact", items=[*items(), impact])
    run.system = system
    result = await run_eval(run)
    assert [r.question_id for r in result.records] == ["r-impact-001"]
    record = result.records[0]
    assert system.targets == [ImpactTarget(symbol_key=F)]
    assert record.output.kind == "impact"
    assert record.score.primary == pytest.approx(2 / 3)  # g found, plus one wrong item


def test_cost_meter_stops_after_the_first_refusal() -> None:
    meter = CostMeter(1.0)
    assert meter.reserve(0.6)
    assert not meter.reserve(0.5)
    assert not meter.reserve(0.01)  # stopped
    meter.settle(0.6, 0.2)
    assert meter.spent == 0.2 and meter.reserved == 0.0 and meter.largest == 0.2
    assert CostMeter(None).reserve(1e9)
