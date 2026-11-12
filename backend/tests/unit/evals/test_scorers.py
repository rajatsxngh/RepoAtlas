from __future__ import annotations

import pytest

from repoatlas.bench.schema import (
    AnswerSetTruth,
    FlowTruth,
    ImpactTruth,
    Location,
    LookupTruth,
    RealFailingTest,
    UnanswerableTruth,
)
from repoatlas.evals.outputs import JudgeScore, PredictedClaim, SupportLabel, SystemOutput
from repoatlas.evals.registry import EvalItem, ImpactItem
from repoatlas.evals.scorers import (
    aggregate_outputs,
    is_test_key,
    lookup_correct,
    mean,
    must_cite_recall,
    percentile,
    question_target,
    recall_at_k,
    refusal_outcome,
    score_benchmark,
    set_prf,
)
from repoatlas.retrieval.evidence import EvidenceRef

OID = "a" * 40
DEF = Location(symbol_key="py:src/m.py:f:function", path="src/m.py", line=10)


def ref(path: str, start: int, end: int, key: str | None = None) -> EvidenceRef:
    return EvidenceRef(blob_oid=OID, path=path, start_line=start, end_line=end, symbol_key=key)


def item(type_: str, truth: object, text: str = "?") -> EvalItem:
    return EvalItem(
        id=f"r-{type_}-001",
        type=type_,
        text=text,
        repo_id="r",
        commit="c" * 40,
        split="dev",
        truth=truth,
    )


# --- set_prf, recall_at_k, must_cite_recall --------------------------------


def test_set_prf_counts_hits_once_and_handles_empty_sets() -> None:
    prf = set_prf(["a", "b", "b", "c"], ["a", "c", "d", "e"])
    assert prf.precision == pytest.approx(2 / 3)
    assert prf.recall == pytest.approx(0.5)
    assert prf.f1 == pytest.approx(2 * (2 / 3) * 0.5 / (2 / 3 + 0.5))
    empty_answer = set_prf([], ["a"])
    assert (empty_answer.precision, empty_answer.recall, empty_answer.f1) == (0.0, 0.0, 0.0)
    assert set_prf([], []).f1 == 1.0 and set_prf([], []).recall is None
    nothing_to_find = set_prf(["x"], [])
    assert (nothing_to_find.precision, nothing_to_find.f1) == (0.0, 0.0)
    assert set_prf(["z"], ["a"]).f1 == 0.0


def test_recall_at_k_uses_distinct_ranked_predictions() -> None:
    assert recall_at_k(["a", "a", "x", "b"], {"a", "b"}, 2) == 0.5
    assert recall_at_k(["a", "a", "x", "b"], {"a", "b"}, 3) == 1.0
    assert recall_at_k([], {"a"}, 5) == 0.0
    assert recall_at_k(["a"], set(), 5) is None
    with pytest.raises(ValueError):
        recall_at_k(["a"], {"a"}, 0)


def test_must_cite_recall() -> None:
    assert must_cite_recall(["a", "a", "z"], ["a", "b"]) == 0.5
    with pytest.raises(ValueError):
        must_cite_recall(["a"], [])


# --- lookup ------------------------------------------------------------------


def test_lookup_is_correct_when_a_citation_contains_the_definition_line() -> None:
    assert lookup_correct(DEF, SystemOutput(citations=[ref("src/m.py", 5, 12)]))
    assert lookup_correct(
        DEF, SystemOutput(citations=[ref("src/x.py", 1, 99), ref("src/m.py", 10, 10)])
    )
    assert not lookup_correct(DEF, SystemOutput(citations=[ref("src/m.py", 11, 30)]))
    assert not lookup_correct(DEF, SystemOutput(citations=[ref("src/other.py", 1, 99)]))
    assert not lookup_correct(DEF, SystemOutput())  # empty answer


def test_lookup_without_citations_needs_the_symbol_and_its_path() -> None:
    named = SystemOutput(answer_markdown="`f` is in src/m.py", answer_keys=[DEF.symbol_key])
    assert lookup_correct(DEF, named)
    assert not lookup_correct(DEF, named.model_copy(update={"answer_markdown": "`f`"}))
    refused = named.model_copy(update={"outcome": "refused_evidence"})
    assert not lookup_correct(DEF, refused)


# --- refusals ------------------------------------------------------------------


def test_only_evidence_refusals_are_correct_for_unanswerable_questions() -> None:
    q = item("unanswerable", UnanswerableTruth())
    evidence = score_benchmark(q, SystemOutput(outcome="refused_evidence"))
    api = score_benchmark(q, SystemOutput(outcome="refused_api"))
    answered = score_benchmark(q, SystemOutput(outcome="answered"))
    error = score_benchmark(q, SystemOutput(outcome="error", error="boom"))
    assert (evidence.primary, api.primary, answered.primary, error.primary) == (1.0, 0.0, 0.0, None)
    assert api.metrics["api_refusal"] == 1.0 and evidence.metrics["api_refusal"] == 0.0
    assert refusal_outcome(SystemOutput(outcome="refused_budget")) == "budget_refusal"


def test_a_refused_answerable_question_scores_zero_and_an_error_none() -> None:
    q = item("lookup", LookupTruth(definition=DEF))
    assert score_benchmark(q, SystemOutput(outcome="refused_evidence")).primary == 0.0
    assert score_benchmark(q, SystemOutput(outcome="refused_api")).primary == 0.0
    assert score_benchmark(q, SystemOutput(outcome="error", error="x")).primary is None


# --- lists ---------------------------------------------------------------------


CALLER_A = Location(symbol_key="py:src/a.py:a:function", path="src/a.py", line=3)
CALLER_B = Location(symbol_key="py:src/b.py:B.b:method", path="src/b.py", line=7)


def test_callers_score_the_claimed_symbols_without_the_target() -> None:
    q = item(
        "callers",
        AnswerSetTruth(answers=[CALLER_A, CALLER_B]),
        "Which functions call `f` (defined in `src/m.py`)?",
    )
    output = SystemOutput(
        answer_keys=[
            CALLER_A.symbol_key,
            DEF.symbol_key,
            CALLER_A.symbol_key,
            "py:src/c.py:c:function",
        ]
    )
    score = score_benchmark(q, output)
    assert score.metrics["precision"] == 0.5  # a and c; the target f is not counted
    assert score.metrics["recall"] == 0.5
    assert score.primary == pytest.approx(0.5)


def test_question_target_reads_name_and_path() -> None:
    text = "Which tests reach `Context.invoke` (defined in `src/click/core.py`) directly?"
    assert question_target(text) == ("Context.invoke", "src/click/core.py")
    assert question_target("Where is `f` defined?") is None


# --- impact --------------------------------------------------------------------


TEST_1 = "py:tests/t.py:test_one:test"
TEST_2 = "py:tests/t.py:test_two:test"


def impact_truth(real: list[RealFailingTest] | None) -> ImpactTruth:
    return ImpactTruth(
        target=DEF,
        static=[CALLER_A, Location(symbol_key=TEST_1, path="tests/t.py", line=1)],
        real_failing_tests=real,
    )


def test_impact_scores_static_set_and_real_failing_tests() -> None:
    real = [
        RealFailingTest(test_id="tests/t.py::test_one[1]", symbol_key=TEST_1),
        RealFailingTest(test_id="tests/t.py::test_one[2]", symbol_key=TEST_1),
        RealFailingTest(test_id="tests/t.py::test_two", symbol_key=TEST_2),
        RealFailingTest(test_id="unmapped", symbol_key=None),
    ]
    q = item("impact", impact_truth(real))
    output = SystemOutput(answer_keys=[DEF.symbol_key, CALLER_A.symbol_key, TEST_1])
    score = score_benchmark(q, output)
    assert score.primary == 1.0  # the target is dropped; a and test_one are the static set
    assert score.metrics["real_precision"] == 1.0
    assert score.metrics["real_recall"] == 0.5
    assert score.metrics["real_recall_at_10"] == 0.5
    no_real = score_benchmark(item("impact", impact_truth(None)), output)
    assert "real_recall" not in no_real.metrics


def test_impact_systems_rank_their_test_items() -> None:
    real = [RealFailingTest(test_id="x", symbol_key=TEST_2)]
    q = item("impact", impact_truth(real))
    output = SystemOutput(
        kind="impact",
        impact=[
            ImpactItem(symbol_key=CALLER_A.symbol_key, group="caller", score=0.9),
            ImpactItem(symbol_key=TEST_2, group="test", score=0.8),
        ],
    )
    score = score_benchmark(q, output)
    assert score.metrics["static_precision"] == 0.5
    assert score.metrics["static_recall"] == 0.5
    assert score.metrics["real_recall"] == 1.0


def test_is_test_key() -> None:
    assert is_test_key(TEST_1)
    assert is_test_key("py:tests/t.py:test_one:test~2")
    assert not is_test_key(DEF.symbol_key)
    assert not is_test_key("file:src/m.py")


# --- flow ----------------------------------------------------------------------


def test_flow_scores_must_cite_recall_and_carries_the_judge() -> None:
    must = [
        DEF,
        CALLER_A,
        CALLER_B,
    ]
    q = item("flow", FlowTruth(must_cite=must, reference_outline="1. f calls a"))
    judge = JudgeScore(steps_correct=2, coverage=1, grounded=2, total=5 / 6)
    output = SystemOutput(
        cited_keys=[DEF.symbol_key, DEF.symbol_key], answer_keys=[CALLER_A.symbol_key], judge=judge
    )
    score = score_benchmark(q, output)
    assert score.primary == pytest.approx(2 / 3)
    assert score.metrics["judge"] == pytest.approx(5 / 6)
    refused = score_benchmark(q, output.model_copy(update={"outcome": "refused_evidence"}))
    assert refused.primary == 0.0 and refused.metrics["judge"] == 0.0


# --- aggregates ------------------------------------------------------------------


def test_aggregates_pool_citations_support_cost_and_latency() -> None:
    outputs = [
        SystemOutput(
            prefilter_citation_validity=1.0,
            citations_total=4,
            citations_valid=4,
            dropped_claim_rate=0.0,
            cost_usd=0.02,
            eval_cost_usd=0.01,
            latency_s=1.0,
            support=[
                SupportLabel(claim_id="c1", label="supported"),
                SupportLabel(claim_id="c2", label="partial"),
            ],
            claims=[PredictedClaim(id="c1", text="x"), PredictedClaim(id="c2", text="y")],
        ),
        SystemOutput(
            prefilter_citation_validity=0.5,
            citations_total=4,
            citations_valid=2,
            dropped_claim_rate=0.5,
            cost_usd=0.04,
            latency_s=3.0,
            support=[SupportLabel(claim_id="c1", label="unsupported")],
        ),
        SystemOutput(outcome="error", error="x", latency_s=99.0),
    ]
    agg = aggregate_outputs(outputs)
    assert agg.outcomes == {"answered": 2, "error": 1}
    assert agg.prefilter_citation_validity == 0.75
    assert agg.pooled_citation_validity == 0.75
    assert agg.dropped_claim_rate == 0.25
    assert agg.claims_checked == 3
    assert agg.claim_supported == pytest.approx(1 / 3)
    assert agg.claim_partial == pytest.approx(1 / 3)
    assert agg.cost_per_question_usd == pytest.approx(0.02)
    assert agg.total_cost_usd == pytest.approx(0.07)
    assert agg.latency_p50_s == 2.0  # errors are left out
    assert agg.latency_p95_s == pytest.approx(2.9)


def test_mean_and_percentile_edge_cases() -> None:
    assert mean([None, None]) is None
    assert mean([1.0, None, 3.0]) == 2.0
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_a_claim_tagged_with_the_target_counts_the_symbols_it_cites() -> None:
    q = item(
        "callers",
        AnswerSetTruth(answers=[CALLER_A]),
        "Which functions call `f` (defined in `src/m.py`)?",
    )
    claim = PredictedClaim(
        id="c1",
        text="f is applied on a.",
        symbol="f",
        refs=[ref("src/a.py", 3, 5)],
        keys=[],
        ref_keys=[CALLER_A.symbol_key],
    )
    definition = PredictedClaim(
        id="c2",
        text="f is defined here.",
        symbol="f",
        refs=[ref("src/m.py", 10, 12)],
        keys=[DEF.symbol_key],
        ref_keys=[DEF.symbol_key],
    )
    score = score_benchmark(q, SystemOutput(claims=[claim, definition]))
    assert score.primary == 1.0  # a counts; f itself (the target) never does
