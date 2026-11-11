"""Answer contracts (ANS-6, PRD §9.4, decision 0011).

The model fills ``AnswerDraft``, a flat structure: the answer in markdown, and claims that each
cite evidence IDs (``E1``…``En``) from the evidence it was given. The server maps the IDs to
evidence references, validates them, and returns ``AnswerResult``: only claims with at least
one valid citation, their ``Citation``s, the refusal (if any) and its kind, usage, cost, the
trace ID, and the metrics measured before filtering.

Limits that structured outputs can't express (lengths, counts) are Pydantic constraints here;
the schema sent to the model has them removed (``repoatlas.llm.compat.output_schema``).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from repoatlas.llm.compat import output_schema
from repoatlas.llm.types import Usage
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import EvidenceItem

MAX_CLAIMS = 12
MAX_CITATIONS_PER_CLAIM = 6
MAX_ANSWER_CHARS = 8_000
MAX_CLAIM_CHARS = 1_000

RefusalKind = Literal["evidence", "api", "budget"]


class Claim(BaseModel):
    """One statement in the answer, citing the evidence that supports it. ``symbol`` names the
    identifier the claim is about, when there is one (it must appear in a cited range)."""

    id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS)
    evidence_ids: list[str] = Field(max_length=MAX_CITATIONS_PER_CLAIM)
    symbol: str | None = Field(default=None, max_length=200)


class AnswerDraft(BaseModel):
    """What the model returns."""

    answer_markdown: str = Field(max_length=MAX_ANSWER_CHARS)
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    refused: bool
    refusal_reason: str | None = Field(default=None, max_length=1_000)


def answer_draft_schema() -> dict[str, Any]:
    """The flat JSON schema the model fills."""
    return output_schema(AnswerDraft)


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    evidence_id: str
    ref: EvidenceRef


class AnswerMetrics(BaseModel):
    """Measured before invalid citations are removed (decision 0011). Rates are ``None`` when
    there was nothing to measure (no claims, no citations)."""

    prefilter_citation_validity: float | None = None
    dropped_claim_rate: float | None = None
    citations_total: int = 0
    citations_valid: int = 0
    claims_total: int = 0
    claims_dropped: int = 0
    evidence_count: int = 0
    retrieval_top_score: float | None = None


class AnswerResult(BaseModel):
    answer_markdown: str
    claims: list[Claim]
    citations: list[Citation]
    refused: bool
    refusal_kind: RefusalKind | None = None
    refusal_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    trace_id: uuid.UUID | None = None
    metrics: AnswerMetrics = Field(default_factory=AnswerMetrics)
    draft: AnswerDraft | None = None  # what the model returned, before validation


class Budget(BaseModel):
    """Limits for one answer. A call that could exceed them is not made (``budget`` refusal)."""

    model_config = ConfigDict(frozen=True)

    max_cost_usd: float = Field(default=0.25, gt=0)
    max_output_tokens: int = Field(default=4_096, ge=256)


class NumberedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    item: EvidenceItem

    @property
    def ref(self) -> EvidenceRef:
        return self.item.ref


def number_evidence(items: list[EvidenceItem]) -> dict[str, NumberedEvidence]:
    """``E1``…``En`` for the evidence in the order given (the retriever's score order)."""
    return {
        f"E{index}": NumberedEvidence(id=f"E{index}", item=item)
        for index, item in enumerate(items, start=1)
    }
