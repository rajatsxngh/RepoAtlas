"""Refusal policy: one test per refusal kind, and the cases that proceed."""

from __future__ import annotations

from repoatlas.answers.policy import RefusalPolicy, top_score
from repoatlas.answers.schema import AnswerDraft, Budget, Claim, number_evidence
from repoatlas.answers.validate import CitationValidator, ValidationReport
from repoatlas.config import Settings
from repoatlas.llm.fake import refusal
from repoatlas.llm.types import LLMResult
from repoatlas.retrieval.retriever import EvidenceItem, RetrievalDebug, RetrievalResult
from tests.unit.answers.helpers import EVIDENCE, FakeBlobs


def retrieval(items: list[EvidenceItem]) -> RetrievalResult:
    debug = RetrievalDebug(
        query="q", mode="hybrid", model=None, keyword_query=None, budget_tokens=1, k=1
    )
    return RetrievalResult(evidence=items, debug=debug)


def scored(score: float) -> EvidenceItem:
    return EvidenceItem.model_validate({**EVIDENCE[0].model_dump(), "score": score})


POLICY = RefusalPolicy(min_retrieval_score=0.02)


def test_weak_retrieval_is_an_evidence_refusal_before_the_call() -> None:
    decision = POLICY.pre_check(retrieval([scored(0.0164), scored(0.01)]))
    assert decision.refuse
    assert decision.kind == "evidence"
    assert decision.reason is not None and "0.0164" in decision.reason


def test_no_evidence_is_an_evidence_refusal() -> None:
    decision = POLICY.pre_check(retrieval([]))
    assert (decision.refuse, decision.kind) == (True, "evidence")
    assert top_score(retrieval([])) is None


def test_strong_retrieval_proceeds() -> None:
    assert not POLICY.pre_check(retrieval([scored(0.01), scored(0.033)])).refuse
    assert not POLICY.pre_check(retrieval([scored(0.02)])).refuse  # the threshold itself passes


def report(*claims: Claim) -> ValidationReport:
    draft = AnswerDraft(answer_markdown="a", claims=list(claims), refused=False)
    return CitationValidator(FakeBlobs()).validate(draft, number_evidence(EVIDENCE))


def test_no_surviving_claim_is_an_evidence_refusal_after_validation() -> None:
    decision = POLICY.post_check(report(Claim(id="c1", text="t", evidence_ids=["E9"])))
    assert (decision.refuse, decision.kind) == (True, "evidence")
    assert decision.reason is not None and "1 claims" in decision.reason
    empty = POLICY.post_check(report())
    assert empty.refuse and empty.reason is not None and "no claims" in empty.reason


def test_a_model_that_finds_the_evidence_insufficient_is_an_evidence_refusal() -> None:
    decision = POLICY.post_check(report(), model_refused=True, reason="Nothing about Stripe.")
    assert (decision.kind, decision.reason) == ("evidence", "Nothing about Stripe.")


def test_valid_claims_proceed() -> None:
    assert not POLICY.post_check(report(Claim(id="c1", text="t", evidence_ids=["E1"]))).refuse


def test_a_refusal_stop_reason_is_an_api_refusal() -> None:
    decision = POLICY.api_check(refusal(category="cyber"))
    assert (decision.refuse, decision.kind) == (True, "api")
    assert decision.reason is not None and "cyber" in decision.reason
    assert not POLICY.api_check(LLMResult(content="x", model="m")).refuse


def test_a_call_that_could_exceed_the_budget_is_a_budget_refusal() -> None:
    budget = Budget(max_cost_usd=0.10)
    decision = POLICY.budget_check(budget, spent_usd=0.05, next_call_usd=0.06)
    assert (decision.refuse, decision.kind) == (True, "budget")
    assert not POLICY.budget_check(budget, spent_usd=0.0, next_call_usd=0.10).refuse


def test_threshold_from_settings() -> None:
    settings = Settings(_env_file=None, refusal_min_retrieval_score=0.5)
    assert RefusalPolicy.from_settings(settings).min_retrieval_score == 0.5
