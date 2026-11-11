"""The answer smoke run (M07): a fixed set of questions on one pinned repo, answered live with a
cost cap, summarized with the numbers decision 0011 asks for.

``eval/smoke/questions.yaml`` lists the questions: answerable ones name the symbols a good
answer cites (noted by hand), unanswerable ones must be refused for lack of evidence.
``run_smoke`` answers them in order, stops before a question whose worst case could take the
total past ``max_cost_usd``, stores every run in ``qa_runs``, and checks that each run's trace
has an LLM span with tokens, cost, and latency for every model call.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from repoatlas.answers.runs import save_run
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.system import AnswerSystem
from repoatlas.tracing.store import load_trace


class SmokeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    answerable: bool
    expected_symbols: list[str] = Field(default_factory=list)  # symbol keys a good answer cites
    note: str | None = None


class SmokeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    commit: str
    questions: list[SmokeQuestion]


def load_questions(path: Path) -> SmokeSet:
    return SmokeSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class SpanCheck(BaseModel):
    llm_spans: int
    complete: bool  # every LLM span has tokens, cost, and latency


class SmokeItem(BaseModel):
    id: str
    question: str
    answerable: bool
    status: Literal["answered", "refused", "skipped", "error"]
    refusal_kind: str | None = None
    refusal_reason: str | None = None
    prefilter_citation_validity: float | None = None
    dropped_claim_rate: float | None = None
    claims: int = 0
    citations: int = 0
    expected_cited: bool | None = None
    retrieval_top_score: float | None = None
    cost_usd: float = 0.0
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    run_id: int | None = None
    trace_id: uuid.UUID | None = None
    spans: SpanCheck | None = None
    error: str | None = None


class SmokeSummary(BaseModel):
    repo: str
    commit: str
    snapshot_id: int
    max_cost_usd: float
    items: list[SmokeItem]
    total_cost_usd: float
    answered: int
    refused: int
    skipped: int
    errors: int
    unanswerable_refused_for_evidence: int
    unanswerable_total: int
    answerable_answered: int
    answerable_total: int
    expected_cited: int
    mean_prefilter_citation_validity: float | None
    mean_dropped_claim_rate: float | None
    pooled_prefilter_citation_validity: float | None
    latency_p50_s: float | None
    latency_max_s: float | None
    every_answer_has_a_trace: bool
    every_llm_call_has_a_span: bool


def _cited_expected(result: AnswerResult, expected: list[str]) -> bool | None:
    if not expected:
        return None
    cited = {citation.ref.symbol_key for citation in result.citations}
    return bool(cited & set(expected))


def _span_check(engine: Engine, result: AnswerResult) -> SpanCheck | None:
    """The run's LLM spans, and whether each has tokens, cost, and latency (an answer that
    cost something must have at least one)."""
    if result.trace_id is None:
        return None
    trace = load_trace(engine, result.trace_id)
    if trace is None:
        return SpanCheck(llm_spans=0, complete=False)
    llm = [span for span in trace.spans if span.kind == "llm"]
    complete = all(
        span.tokens_in is not None
        and span.tokens_out is not None
        and span.cost_usd is not None
        and "latency_ms" in span.attrs
        for span in llm
    ) and (result.cost_usd == 0 or bool(llm))
    return SpanCheck(llm_spans=len(llm), complete=complete)


async def run_smoke(
    system: AnswerSystem,
    questions: SmokeSet,
    *,
    snapshot_id: int,
    engine: Engine,
    max_cost_usd: float,
    budget: Budget,
    worst_case_usd: float,
    progress: Callable[[SmokeItem], None] | None = None,
) -> SmokeSummary:
    """Answer every question that fits the cost cap; ``worst_case_usd`` is the most one answer
    can cost (questions stop once it no longer fits under the cap)."""
    items: list[SmokeItem] = []
    spent = 0.0
    pooled_valid = pooled_total = 0
    for question in questions.questions:
        if spent + worst_case_usd > max_cost_usd:
            item = SmokeItem(
                id=question.id,
                question=question.question,
                answerable=question.answerable,
                status="skipped",
                error=f"The cost cap (${max_cost_usd:.2f}) could be exceeded.",
            )
        else:
            started = time.perf_counter()
            try:
                result = await system.answer(snapshot_id, question.question, budget)
            except Exception as exc:
                item = SmokeItem(
                    id=question.id,
                    question=question.question,
                    answerable=question.answerable,
                    status="error",
                    latency_s=round(time.perf_counter() - started, 2),
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            else:
                latency = round(time.perf_counter() - started, 2)
                spent += result.cost_usd
                run_id = save_run(engine, snapshot_id, "oneshot", question.question, result)
                metrics = result.metrics
                pooled_valid += metrics.citations_valid
                pooled_total += metrics.citations_total
                item = SmokeItem(
                    id=question.id,
                    question=question.question,
                    answerable=question.answerable,
                    status="refused" if result.refused else "answered",
                    refusal_kind=result.refusal_kind,
                    refusal_reason=result.refusal_reason,
                    prefilter_citation_validity=metrics.prefilter_citation_validity,
                    dropped_claim_rate=metrics.dropped_claim_rate,
                    claims=len(result.claims),
                    citations=len(result.citations),
                    expected_cited=_cited_expected(result, question.expected_symbols),
                    retrieval_top_score=metrics.retrieval_top_score,
                    cost_usd=result.cost_usd,
                    latency_s=latency,
                    input_tokens=result.usage.total_input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cache_read_tokens=result.usage.cache_read_tokens,
                    run_id=run_id,
                    trace_id=result.trace_id,
                    spans=_span_check(engine, result),
                )
        items.append(item)
        if progress is not None:
            progress(item)
    return summarize(questions, snapshot_id, max_cost_usd, items, pooled_valid, pooled_total)


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(statistics.fmean(present), 4) if present else None


def summarize(
    questions: SmokeSet,
    snapshot_id: int,
    max_cost_usd: float,
    items: list[SmokeItem],
    pooled_valid: int,
    pooled_total: int,
) -> SmokeSummary:
    ran = [item for item in items if item.status in ("answered", "refused")]
    unanswerable = [item for item in items if not item.answerable]
    answerable = [item for item in items if item.answerable]
    latencies = sorted(item.latency_s for item in ran)
    return SmokeSummary(
        repo=questions.repo,
        commit=questions.commit,
        snapshot_id=snapshot_id,
        max_cost_usd=max_cost_usd,
        items=items,
        total_cost_usd=round(sum(item.cost_usd for item in items), 6),
        answered=sum(item.status == "answered" for item in items),
        refused=sum(item.status == "refused" for item in items),
        skipped=sum(item.status == "skipped" for item in items),
        errors=sum(item.status == "error" for item in items),
        unanswerable_refused_for_evidence=sum(
            item.status == "refused" and item.refusal_kind == "evidence" for item in unanswerable
        ),
        unanswerable_total=len(unanswerable),
        answerable_answered=sum(item.status == "answered" for item in answerable),
        answerable_total=len(answerable),
        expected_cited=sum(bool(item.expected_cited) for item in answerable),
        mean_prefilter_citation_validity=_mean([i.prefilter_citation_validity for i in ran]),
        mean_dropped_claim_rate=_mean([i.dropped_claim_rate for i in ran]),
        pooled_prefilter_citation_validity=(
            round(pooled_valid / pooled_total, 4) if pooled_total else None
        ),
        latency_p50_s=statistics.median(latencies) if latencies else None,
        latency_max_s=latencies[-1] if latencies else None,
        every_answer_has_a_trace=all(item.trace_id is not None for item in ran),
        every_llm_call_has_a_span=all(
            item.spans is not None and item.spans.complete for item in ran
        ),
    )


def summary_lines(summary: SmokeSummary) -> list[str]:
    """A plain-text table of the run."""

    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value * 100:.0f}%"

    lines = [
        f"{summary.repo} @ {summary.commit[:7]} (snapshot {summary.snapshot_id}), "
        f"cost cap ${summary.max_cost_usd:.2f}",
        "",
        f"{'id':<6} {'kind':<6} {'result':<17} {'valid':>6} {'dropped':>8} {'cited':>6} "
        f"{'cost':>8} {'time':>6}",
    ]
    for item in summary.items:
        result = item.status if item.status != "refused" else f"refused/{item.refusal_kind}"
        cited = "-" if item.expected_cited is None else ("yes" if item.expected_cited else "no")
        lines.append(
            f"{item.id:<6} {'ans' if item.answerable else 'unans':<6} {result:<17} "
            f"{pct(item.prefilter_citation_validity):>6} {pct(item.dropped_claim_rate):>8} "
            f"{cited:>6} {item.cost_usd:>8.4f} {item.latency_s:>5.1f}s"
        )
    lines += [
        "",
        f"Answered {summary.answered}, refused {summary.refused}, skipped {summary.skipped}, "
        f"errors {summary.errors}.",
        f"Unanswerable refused for evidence: {summary.unanswerable_refused_for_evidence} of "
        f"{summary.unanswerable_total}. Answerable answered: {summary.answerable_answered} of "
        f"{summary.answerable_total}; expected symbol cited in {summary.expected_cited}.",
        f"Pre-filter citation validity: mean {pct(summary.mean_prefilter_citation_validity)}, "
        f"pooled {pct(summary.pooled_prefilter_citation_validity)}. Dropped-claim rate: mean "
        f"{pct(summary.mean_dropped_claim_rate)}.",
        f"Cost ${summary.total_cost_usd:.4f}. Latency median {summary.latency_p50_s or 0:.1f}s, "
        f"max {summary.latency_max_s or 0:.1f}s.",
        f"Every answer has a trace: {summary.every_answer_has_a_trace}. Every LLM call has a "
        f"span with tokens, cost, and latency: {summary.every_llm_call_has_a_span}.",
    ]
    return lines
