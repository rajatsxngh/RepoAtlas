"""A system's output for one question, in the form scorers read, and one question's score.

Answer systems return ``AnswerResult``; impact systems return ``ImpactPrediction``. The runner
turns either into a ``SystemOutput``: citations mapped to symbol keys on the evaluated snapshot
(``repoatlas.evals.mapping``), the predicted answer set, refusal kind, and measured metrics.
Scorers are pure functions of a question and its ``SystemOutput``, so they are tested with
synthetic outputs and re-run on cached outputs for free.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from repoatlas.evals.registry import ImpactItem
from repoatlas.retrieval.evidence import EvidenceRef

Outcome = Literal[
    "answered",
    "refused_evidence",
    "refused_api",
    "refused_budget",
    "error",
]
SupportValue = Literal["supported", "partial", "unsupported"]


class PredictedClaim(BaseModel):
    """One claim of an answer. ``keys`` are the symbol keys the claim names as answers: the cited
    symbols matching its ``symbol`` tag (or, with no citations, the tag resolved by name)."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    symbol: str | None = None
    refs: list[EvidenceRef] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)
    ref_keys: list[str] = Field(default_factory=list)  # the symbol around each cited range


class SupportLabel(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    label: SupportValue
    reason: str = ""


class JudgeScore(BaseModel):
    """The flow judge's rubric scores (each 0-2) and their normalized total in [0, 1]."""

    model_config = ConfigDict(frozen=True)

    steps_correct: int = Field(ge=0, le=2)
    coverage: int = Field(ge=0, le=2)
    grounded: int = Field(ge=0, le=2)
    total: float = Field(ge=0, le=1)
    rationale: str = ""


class SystemOutput(BaseModel):
    kind: Literal["answer", "impact"] = "answer"
    outcome: Outcome = "answered"
    answer_markdown: str = ""
    refusal_reason: str | None = None
    claims: list[PredictedClaim] = Field(default_factory=list)
    citations: list[EvidenceRef] = Field(default_factory=list)  # valid ones, first use order
    cited_keys: list[str] = Field(default_factory=list)  # symbols of the citations, deduplicated
    answer_keys: list[str] = Field(default_factory=list)  # the predicted set, in claim order
    impact: list[ImpactItem] | None = None  # impact systems: items in rank order
    # Measured by the answer system before invalid citations were removed.
    prefilter_citation_validity: float | None = None
    dropped_claim_rate: float | None = None
    citations_total: int = 0
    citations_valid: int = 0
    claims_total: int = 0
    claims_dropped: int = 0
    evidence_count: int = 0
    # Added by the checker and the judge when they run.
    support: list[SupportLabel] | None = None
    judge: JudgeScore | None = None
    cost_usd: float = 0.0  # the system's own spend
    eval_cost_usd: float = 0.0  # checker and judge spend
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    notes: list[str] = Field(default_factory=list)  # for example, a checker that failed

    @property
    def refused(self) -> bool:
        return self.outcome.startswith("refused")


class ItemScore(BaseModel):
    """One question's score for one run. ``primary`` is the type's main metric in [0, 1] (``None``
    when it can't be computed, for example after an error); ``metrics`` holds the rest."""

    model_config = ConfigDict(frozen=True)

    question_id: str
    type: str
    run: int = 0
    outcome: Outcome
    primary: float | None
    metrics: dict[str, float | None] = Field(default_factory=dict)
