"""Cited explanations of impact reports (IMP-8), through the answer system's building blocks.

The report's changed symbols and top items (best first, a few per group) become numbered
evidence: each one's span (or, for a long span, the lines around its evidence site), read from
the stored blobs. The question lists the ranked items with their groups, scores, and how they
are reached, and asks the ``large`` model for a short cited answer ("Changing X affects …").
The answer prompt, citation validator, and refusal policy are the ones every answer uses
(decisions 0010 and 0011): a report with nothing to cite is an ``evidence`` refusal without an
LLM call; a call that could exceed the budget is a ``budget`` refusal; a refusal stop reason is
an ``api`` refusal; claims without a valid citation are dropped.
"""

from __future__ import annotations

import anyio

from repoatlas.answers.policy import RefusalDecision, RefusalPolicy
from repoatlas.answers.prompting import build_answer_messages, estimate_message_tokens
from repoatlas.answers.schema import (
    AnswerDraft,
    AnswerMetrics,
    AnswerResult,
    Budget,
    number_evidence,
)
from repoatlas.answers.validate import BlobSource, CitationValidator
from repoatlas.graph.model import Node
from repoatlas.impact.data import ImpactData
from repoatlas.impact.schema import GROUPS, ImpactItem, ImpactReport
from repoatlas.llm.client import LLM
from repoatlas.llm.pricing import PriceTable
from repoatlas.llm.types import LLMResult, Usage
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import EvidenceItem
from repoatlas.store.blobs import split_lines
from repoatlas.tracing import TraceSink, start_trace, trace_span

REPAIR_FACTOR = 2
PER_GROUP = 4  # items per group that become evidence
MAX_SPAN_LINES = 40  # longer spans are cut to the lines around the evidence site
SITE_CONTEXT = 6
GROUP_TITLES = {"test": "tests", "public_api": "public APIs", "caller": "callers"}

NOTHING_TEXT = "The impact report has no code to cite, so there is nothing to explain."
UNVERIFIED_TEXT = (
    "An explanation was drafted, but none of its statements could be verified against the "
    "cited code, so it isn't shown."
)
API_REFUSAL_TEXT = "The model declined to explain this report."
BUDGET_TEXT = "Explaining this report could exceed the cost budget."


class ImpactExplainer:
    def __init__(
        self,
        data: ImpactData,
        blobs: BlobSource,
        llm: LLM,
        *,
        prices: PriceTable,
        policy: RefusalPolicy | None = None,
        sink: TraceSink | None = None,
        per_group: int = PER_GROUP,
    ) -> None:
        self.data = data
        self.blobs = blobs
        self.llm = llm
        self.prices = prices
        self.validator = CitationValidator(blobs)
        self.policy = policy or RefusalPolicy()
        self.sink = sink
        self.per_group = per_group

    async def explain(
        self, snapshot_id: int, report: ImpactReport, budget: Budget | None = None
    ) -> AnswerResult:
        budget = budget or Budget()
        with (
            start_trace("impact_explain", snapshot_id, sink=self.sink) as trace,
            trace_span("impact.explain", "answer", items=len(report.items)) as root,
        ):
            result = await self._run(snapshot_id, report, budget)
            root.set(
                refused=result.refused,
                refusal_kind=result.refusal_kind,
                claims=len(result.claims),
                evidence=result.metrics.evidence_count,
            )
        return result.model_copy(update={"trace_id": trace.id})

    def chosen_items(self, report: ImpactReport) -> list[ImpactItem]:
        """The top items of each group, in the combined ranking's order."""
        keep: set[str] = set()
        for group in GROUPS:
            keep.update(item.symbol_key for item in report.group(group)[: self.per_group])
        return [item for item in report.items if item.symbol_key in keep]

    def evidence_items(self, snapshot_id: int, report: ImpactReport) -> list[EvidenceItem]:
        """Evidence for the changed symbols, then for the chosen items."""
        chosen = self.chosen_items(report)
        keys = [*report.targets, *(item.symbol_key for item in chosen)]
        nodes = self.data.nodes(snapshot_id, keys)
        oids = self.data.blob_oids(snapshot_id, sorted({n.path for n in nodes.values()}))
        sites = {item.symbol_key: item.evidence[0] for item in chosen if item.evidence}
        found: list[EvidenceItem] = []
        for rank, key in enumerate(keys):
            node = nodes.get(key)
            oid = oids.get(node.path) if node is not None else None
            if node is None or oid is None:
                continue
            evidence = _span_evidence(node, oid, sites.get(key), self.blobs, rank)
            if evidence is not None:
                found.append(evidence)
        return found

    async def _run(self, snapshot_id: int, report: ImpactReport, budget: Budget) -> AnswerResult:
        with trace_span("impact.evidence", "retrieval") as span:
            items = await anyio.to_thread.run_sync(lambda: self.evidence_items(snapshot_id, report))
            span.set(evidence=len(items))
        metrics = AnswerMetrics(evidence_count=len(items))
        if len(items) <= len(report.targets) and not report.items:
            return _refused(
                RefusalDecision(
                    refuse=True, kind="evidence", reason="The change reaches nothing to explain."
                ),
                NOTHING_TEXT,
                metrics,
            )
        if not items:
            return _refused(
                RefusalDecision(refuse=True, kind="evidence", reason="No code could be read."),
                NOTHING_TEXT,
                metrics,
            )
        evidence = number_evidence(items)
        messages = build_answer_messages(
            impact_question(report, self.chosen_items(report)), evidence
        )
        model = self.llm.model_for("large")
        worst = REPAIR_FACTOR * self.prices.max_cost(
            model, estimate_message_tokens(messages), budget.max_output_tokens
        )
        decision = self.policy.budget_check(budget, spent_usd=0.0, next_call_usd=worst)
        if decision.refuse:
            return _refused(decision, BUDGET_TEXT, metrics)

        llm = await self.llm.complete(
            "large", messages, schema=AnswerDraft, max_tokens=budget.max_output_tokens
        )
        decision = self.policy.api_check(llm)
        if decision.refuse:
            return _refused(decision, API_REFUSAL_TEXT, metrics, llm=llm)
        draft = llm.parsed
        if not isinstance(draft, AnswerDraft):
            raise TypeError("The explanation call returned no AnswerDraft.")
        with trace_span("validate", "validation") as span:
            checked = await anyio.to_thread.run_sync(
                lambda: self.validator.validate(draft, evidence)
            )
            span.set(
                citations=checked.metrics.citations_total, valid=checked.metrics.citations_valid
            )
        decision = self.policy.post_check(
            checked, model_refused=draft.refused, reason=draft.refusal_reason
        )
        if decision.refuse:
            text = draft.answer_markdown if draft.refused and draft.answer_markdown else None
            return _refused(
                decision, text or UNVERIFIED_TEXT, checked.metrics, llm=llm, draft=draft
            )
        return AnswerResult(
            answer_markdown=checked.answer_markdown,
            claims=checked.claims,
            citations=checked.citations,
            refused=False,
            usage=llm.usage,
            cost_usd=llm.cost_usd,
            metrics=checked.metrics,
            draft=draft,
        )


async def explain_impact(
    report: ImpactReport,
    *,
    snapshot_id: int,
    explainer: ImpactExplainer,
    budget: Budget | None = None,
) -> AnswerResult:
    """A cited, validated summary of ``report`` (see ``ImpactExplainer``)."""
    return await explainer.explain(snapshot_id, report, budget)


def impact_question(report: ImpactReport, items: list[ImpactItem]) -> str:
    """The question: what changes, and what the analysis found, for the model to explain."""
    changed = ", ".join(f"`{key}`" for key in report.targets) or "the change"
    lines = [
        f"What breaks if {changed} changes? A static impact analysis of the call, import, "
        "inheritance, and test graph found the symbols below (score 0 to 1: how likely the "
        "change reaches it, combining every path). Summarize in a few sentences which tests, "
        "public APIs, and callers are affected and why, citing the evidence. Say that the "
        "list may be incomplete."
    ]
    for group in GROUPS:
        chosen = [item for item in items if item.group == group]
        if not chosen:
            continue
        lines.append(f"\n{GROUP_TITLES[group].capitalize()}:")
        for item in chosen:
            via = " -> ".join(step.kind for step in item.best_path)
            lines.append(f"- `{item.symbol_key}` score {item.score:.2f}, via {via}")
    if report.unknown:
        lines.append(
            f"\n{len(report.unknown)} more call sites or string literals may refer to it, "
            "but the graph could not resolve them."
        )
    return "\n".join(lines)


def _span_evidence(
    node: Node, oid: str, site: EvidenceRef | None, blobs: BlobSource, rank: int
) -> EvidenceItem | None:
    text = blobs.text(oid)
    span = node.span or node.name_range
    if text is None or span is None:
        return None
    lines = split_lines(text)
    start, end = span.start_line, span.end_line
    if end - start + 1 > MAX_SPAN_LINES:
        if site is not None and start <= site.start_line <= end:
            start = max(start, site.start_line - SITE_CONTEXT)
            end = min(end, site.end_line + SITE_CONTEXT)
        else:
            end = start + MAX_SPAN_LINES - 1
    end = min(end, len(lines))
    if start > end:
        return None
    return EvidenceItem(
        ref=EvidenceRef(
            blob_oid=oid, path=node.path, start_line=start, end_line=end, symbol_key=node.key
        ),
        text="\n".join(lines[start - 1 : end]),
        score=round(1.0 / (1 + rank), 6),
        sources={"graph"},
        node_key=node.key,
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
