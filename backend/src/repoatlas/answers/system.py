"""Answer systems (ANS-10): ``answer(snapshot_id, question, budget) -> AnswerResult``.

``OneShotAnswerSystem`` answers in one LLM call, inside one trace:

1. **retrieving** — hybrid retrieval packs evidence into the token budget; weak or no evidence
   is an ``evidence`` refusal with no LLM call;
2. **answering** — the evidence is numbered ``E1``…``En`` and the ``large`` model fills an
   ``AnswerDraft`` (structured output). A call whose worst-case cost (the call and its one
   repair attempt) could exceed the budget is a ``budget`` refusal; a refusal stop reason is an
   ``api`` refusal;
3. **validating** — citations are checked against the stored blobs; claims without a valid
   citation are dropped, and an answer with none left (or that the model itself declined for
   lack of evidence) is an ``evidence`` refusal that keeps the draft for analysis.

``on_status`` hears each step's name as it starts (the ask endpoint streams them). Answer
systems are registered by name in ``answer_systems``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

import anyio

from repoatlas.answers.policy import RefusalDecision, RefusalPolicy, top_score
from repoatlas.answers.prompting import build_answer_messages, estimate_message_tokens
from repoatlas.answers.schema import (
    AnswerDraft,
    AnswerMetrics,
    AnswerResult,
    Budget,
    number_evidence,
)
from repoatlas.answers.validate import CitationValidator
from repoatlas.llm.client import LLM
from repoatlas.llm.pricing import PriceTable
from repoatlas.llm.types import LLMResult, Usage
from repoatlas.retrieval.retriever import Mode, RetrievalResult
from repoatlas.tracing import TraceSink, start_trace, trace_span

StatusCallback = Callable[[str], Awaitable[None]]
STEPS = ("retrieving", "answering", "validating")
REPAIR_FACTOR = 2  # the client may repeat the call once to repair invalid output

NO_EVIDENCE_TEXT = "The indexed code doesn't show enough to answer this question."
UNVERIFIED_TEXT = (
    "An answer was drafted, but none of its statements could be verified against the cited "
    "code, so it isn't shown."
)
API_REFUSAL_TEXT = "The model declined to answer this question."
BUDGET_TEXT = "Answering this question could exceed the answer's cost budget."


class EvidenceRetriever(Protocol):
    """What the answer system needs from ``Retriever``."""

    def retrieve(
        self, snapshot_id: int, query: str, mode: Mode = ..., budget_tokens: int = ...
    ) -> RetrievalResult: ...


class AnswerSystem(Protocol):
    name: str

    async def answer(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        *,
        on_status: StatusCallback | None = None,
    ) -> AnswerResult: ...


async def _quiet(_step: str) -> None:
    return None


class OneShotAnswerSystem:
    name = "oneshot"

    def __init__(
        self,
        retriever: EvidenceRetriever,
        llm: LLM,
        validator: CitationValidator,
        policy: RefusalPolicy,
        *,
        prices: PriceTable,
        sink: TraceSink | None = None,
        mode: Mode = "hybrid",
        evidence_tokens: int = 12_000,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.validator = validator
        self.policy = policy
        self.prices = prices
        self.sink = sink
        self.mode = mode
        self.evidence_tokens = evidence_tokens

    async def answer(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        *,
        on_status: StatusCallback | None = None,
    ) -> AnswerResult:
        with (
            start_trace("answer", snapshot_id, sink=self.sink) as trace,
            trace_span("answer", "answer", system=self.name) as root,
        ):
            result = await self._run(snapshot_id, question, budget, on_status or _quiet)
            root.set(
                refused=result.refused,
                refusal_kind=result.refusal_kind,
                claims=len(result.claims),
                citations=len(result.citations),
                evidence=result.metrics.evidence_count,
                prefilter_citation_validity=result.metrics.prefilter_citation_validity,
                dropped_claim_rate=result.metrics.dropped_claim_rate,
            )
        return result.model_copy(update={"trace_id": trace.id})

    async def _run(
        self, snapshot_id: int, question: str, budget: Budget, status: StatusCallback
    ) -> AnswerResult:
        await status("retrieving")
        with trace_span("retrieve", "retrieval", mode=self.mode) as span:
            retrieval = await anyio.to_thread.run_sync(
                lambda: self.retriever.retrieve(
                    snapshot_id, question, mode=self.mode, budget_tokens=self.evidence_tokens
                )
            )
            span.set(evidence=len(retrieval.evidence), top_score=top_score(retrieval))
        metrics = AnswerMetrics(
            evidence_count=len(retrieval.evidence), retrieval_top_score=top_score(retrieval)
        )
        decision = self.policy.pre_check(retrieval)
        if decision.refuse:
            return _refused(decision, NO_EVIDENCE_TEXT, metrics)

        evidence = number_evidence(retrieval.evidence)
        messages = build_answer_messages(question, evidence)
        model = self.llm.model_for("large")
        worst = REPAIR_FACTOR * self.prices.max_cost(
            model, estimate_message_tokens(messages), budget.max_output_tokens
        )
        decision = self.policy.budget_check(budget, spent_usd=0.0, next_call_usd=worst)
        if decision.refuse:
            return _refused(decision, BUDGET_TEXT, metrics)

        await status("answering")
        llm = await self.llm.complete(
            "large", messages, schema=AnswerDraft, max_tokens=budget.max_output_tokens
        )
        decision = self.policy.api_check(llm)
        if decision.refuse:
            return _refused(decision, API_REFUSAL_TEXT, metrics, llm=llm)
        draft = llm.parsed
        if not isinstance(draft, AnswerDraft):
            raise TypeError("The answer call returned no AnswerDraft.")

        await status("validating")
        with trace_span("validate", "validation") as span:
            report = await anyio.to_thread.run_sync(
                lambda: self.validator.validate(draft, evidence)
            )
            span.set(
                citations=report.metrics.citations_total,
                valid=report.metrics.citations_valid,
                dropped_claims=report.metrics.claims_dropped,
            )
        metrics = report.metrics.model_copy(
            update={"retrieval_top_score": metrics.retrieval_top_score}
        )
        decision = self.policy.post_check(
            report, model_refused=draft.refused, reason=draft.refusal_reason
        )
        if decision.refuse:
            text = draft.answer_markdown if draft.refused and draft.answer_markdown else None
            return _refused(decision, text or UNVERIFIED_TEXT, metrics, llm=llm, draft=draft)
        return AnswerResult(
            answer_markdown=report.answer_markdown,
            claims=report.claims,
            citations=report.citations,
            refused=False,
            usage=llm.usage,
            cost_usd=llm.cost_usd,
            metrics=metrics,
            draft=draft,
        )


def _refused(
    decision: RefusalDecision,
    text: str,
    metrics: AnswerMetrics,
    *,
    llm: LLMResult | None = None,
    draft: AnswerDraft | None = None,
) -> AnswerResult:
    return AnswerResult(
        answer_markdown=text,
        claims=[],
        citations=[],
        refused=True,
        refusal_kind=decision.kind,
        refusal_reason=decision.reason,
        usage=llm.usage if llm is not None else Usage(),
        cost_usd=llm.cost_usd if llm is not None else 0.0,
        metrics=metrics,
        draft=draft,
    )


AnswerSystemFactory = Callable[..., AnswerSystem]
answer_systems: dict[str, AnswerSystemFactory] = {"oneshot": OneShotAnswerSystem}
