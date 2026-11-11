"""Refusal policy (ANS-9, PRD §9.5, decision 0011).

Refusals have kinds, recorded separately:

- ``evidence``, before the call: no evidence, or the top retrieval score (the best evidence
  item's reranked score: reciprocal-rank fusion plus graph expansion and the exact-name bonus)
  is below ``refusal_min_retrieval_score``. No LLM call is made.
- ``evidence``, after validation: the model said the evidence isn't enough, or no claim kept a
  valid citation. The draft is kept for analysis.
- ``api``: the model stopped with a refusal stop reason.
- ``budget``: the call could exceed the answer's cost budget, so it isn't made.

The score threshold is provisional (set from the smoke run; PRD §9.5 has it tuned on the dev
split's unanswerable questions later). With reciprocal-rank fusion (k = 60), a chunk first in
one list scores about 0.0164 and first in both about 0.033, so the default 0.02 asks that the
best chunk was found by both searches or named exactly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from repoatlas.answers.schema import Budget, RefusalKind
from repoatlas.answers.validate import ValidationReport
from repoatlas.config import Settings
from repoatlas.llm.types import LLMResult
from repoatlas.retrieval.retriever import RetrievalResult

DEFAULT_MIN_RETRIEVAL_SCORE = 0.02


class RefusalDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    refuse: bool
    kind: RefusalKind | None = None
    reason: str | None = None


PROCEED = RefusalDecision(refuse=False)


def top_score(retrieval: RetrievalResult) -> float | None:
    return max((item.score for item in retrieval.evidence), default=None)


class RefusalPolicy:
    def __init__(self, min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE) -> None:
        self.min_retrieval_score = min_retrieval_score

    @classmethod
    def from_settings(cls, settings: Settings) -> RefusalPolicy:
        return cls(min_retrieval_score=settings.refusal_min_retrieval_score)

    def pre_check(self, retrieval: RetrievalResult) -> RefusalDecision:
        score = top_score(retrieval)
        if score is None:
            return RefusalDecision(
                refuse=True, kind="evidence", reason="No code in the snapshot matched the question."
            )
        if score < self.min_retrieval_score:
            return RefusalDecision(
                refuse=True,
                kind="evidence",
                reason=(
                    f"The best match scored {score:.4f}, below the "
                    f"{self.min_retrieval_score:.4f} needed to answer."
                ),
            )
        return PROCEED

    def post_check(
        self, report: ValidationReport, *, model_refused: bool = False, reason: str | None = None
    ) -> RefusalDecision:
        if model_refused:
            return RefusalDecision(
                refuse=True,
                kind="evidence",
                reason=reason or "The model found the evidence insufficient.",
            )
        if not report.claims:
            dropped = report.metrics.claims_dropped
            detail = (
                f"none of its {dropped} claims kept a valid citation"
                if dropped
                else "it made no claims"
            )
            return RefusalDecision(
                refuse=True, kind="evidence", reason=f"The answer was withheld: {detail}."
            )
        return PROCEED

    def api_check(self, result: LLMResult) -> RefusalDecision:
        if result.refused:
            category = f" ({result.refusal_category})" if result.refusal_category else ""
            return RefusalDecision(
                refuse=True, kind="api", reason=f"The model declined the request{category}."
            )
        return PROCEED

    def budget_check(
        self, budget: Budget, *, spent_usd: float, next_call_usd: float
    ) -> RefusalDecision:
        """Refuse a call whose worst-case cost would take the answer past its budget."""
        if spent_usd + next_call_usd > budget.max_cost_usd:
            return RefusalDecision(
                refuse=True,
                kind="budget",
                reason=(
                    f"The next call could cost up to ${next_call_usd:.4f}; with ${spent_usd:.4f} "
                    f"spent that exceeds the ${budget.max_cost_usd:.2f} budget."
                ),
            )
        return PROCEED
