"""``MultiAgentAnswerSystem`` (MAS-1 to MAS-5): the answering mode registered as ``multiagent``.

One answer, inside one trace:

1. **planning**: one search for the question gives the planner its context; the planner
   returns the branches (``repoatlas.multiagent.planner``);
2. **exploring**: every branch is explored, at most three at a time, each inside its share of
   the answer's steps, tokens, and money (``repoatlas.multiagent.explorers``);
3. **writing**: the ledgers are merged and the writer drafts the answer
   (``repoatlas.multiagent.writer``); citations are checked against the evidence it was shown;
4. **verifying**: every remaining claim is read against its cited lines; unsupported claims
   are dropped, partial ones repaired once (``repoatlas.multiagent.verifier``);
5. **validating**: the verified draft passes ``CitationValidator`` like every other answer.

**Budget.** Before the planner is called, the answer's cost budget must cover it and a reserve
for the writer and one verification pass; what is left after the planner is the explorers'
money. Before each later call, its worst case must fit what is left. A call that doesn't fit is
not made: the answer is a ``budget`` refusal, except that a repair that doesn't fit only drops
the partial claims. No claim is ever shown unverified.

**Failures (MAS-5).** A branch that failed, timed out, or was stopped by a limit doesn't fail
the answer. What its ledger holds is used, and the answer ends with a list of the sub-questions
that were not fully explored and why. Only when no branch found anything is there no answer:
the first error is raised if every branch failed with one, otherwise the answer is a refusal
(``api`` if the model declined, ``budget`` if limits stopped the exploring, ``evidence`` if the
tools simply found nothing).

Refusal kinds are the ones every mode uses. The result carries the branches (``branches``), the
verifier's labels (``verification``), the tool calls of every branch (``tool_uses``), and the
cost of each stage (``stage_costs``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from repoatlas.agent.loop import StepCallback, StepEvent, StepKind, ToolCache
from repoatlas.agent.tools import Toolbox, tool_definitions
from repoatlas.answers.policy import RefusalDecision, RefusalPolicy
from repoatlas.answers.schema import (
    AnswerDraft,
    AnswerMetrics,
    AnswerResult,
    BranchSummary,
    Budget,
    ClaimVerdict,
    ToolUse,
    answer_draft_schema,
)
from repoatlas.answers.system import (
    API_REFUSAL_TEXT,
    BUDGET_TEXT,
    DAILY_CAP_TEXT,
    NO_EVIDENCE_TEXT,
    UNVERIFIED_TEXT,
    StatusCallback,
    daily_cap_decision,
)
from repoatlas.answers.validate import BlobSource, CitationValidator
from repoatlas.canonical import digest
from repoatlas.llm.client import LLM
from repoatlas.llm.compat import output_schema
from repoatlas.llm.pricing import PriceTable
from repoatlas.llm.types import LLMResult, SpendCapReached, Usage
from repoatlas.multiagent.explorers import Explorers, branch_prompt
from repoatlas.multiagent.models import (
    BranchNotes,
    BranchResult,
    MultiAgentConfig,
    Plan,
    PlanDraft,
)
from repoatlas.multiagent.planner import Planner, plan_context
from repoatlas.multiagent.prompting import PLANNER_PROMPT
from repoatlas.multiagent.verifier import (
    REPAIR_PROMPT,
    VERIFIER_PROMPT,
    RepairSet,
    VerdictSet,
    Verifier,
    verified_draft,
)
from repoatlas.multiagent.writer import WRITER_PROMPT, Writer
from repoatlas.tracing import TraceSink, start_trace, trace_span

STEPS = ("planning", "exploring", "writing", "verifying", "validating")
STAGES = ("planner", "explorers", "writer", "verifier")
REPAIR_FACTOR = 2
PROMPT_OVERHEAD_TOKENS = 4_000  # rules, notes, and the question, next to the evidence
STOPPED_EARLY_TEXT = (
    "The exploration reached its limits before it found any code to answer from. Try a more "
    "specific question."
)
NOT_VERIFIED_TEXT = (
    "An answer was drafted, but none of its statements was supported by the code it cited, so "
    "it isn't shown."
)
GAPS_HEADING = "**Not fully explored:**"

PlanCallback = Callable[[Plan], Awaitable[None]]


async def _no_status(_step: str) -> None:
    return None


async def _no_step(_event: StepEvent) -> None:
    return None


async def _no_plan(_plan: Plan) -> None:
    return None


def gap_reason(result: BranchResult) -> str | None:
    """Why a branch was not fully explored, in a few words; ``None`` for a complete branch."""
    if result.status == "complete":
        return None
    if result.status == "timeout":
        return "it ran out of time"
    if result.status == "failed":
        return "the model declined it" if result.error == "refused" else "it failed"
    if result.error == "daily_cap":
        return "the server's daily limit was reached"
    return {
        "max_steps": "it reached its step limit",
        "token_budget": "it reached its token budget",
        "spend_budget": "it reached its cost budget",
    }.get(result.stop or "", "it reached a limit")


def gaps_note(results: list[BranchResult]) -> str:
    """The list of sub-questions that were not fully explored ('' when all were)."""
    lines = [
        f"- {result.branch.sub_question} ({reason})"
        for result in results
        if (reason := gap_reason(result)) is not None
    ]
    return "\n".join([GAPS_HEADING, *lines]) if lines else ""


def summarize(result: BranchResult) -> BranchSummary:
    branch = result.branch
    return BranchSummary(
        id=branch.id,
        sub_question=branch.sub_question,
        seed_symbols=branch.seed_symbols,
        status=result.status,
        stop=result.stop,
        error=result.error,
        max_steps=branch.budget.max_steps,
        steps=result.steps,
        evidence=len(result.ledger),
        findings=len(result.notes.findings),
        cost_usd=result.cost_usd,
    )


class _Run:
    """What one answer has spent and produced so far."""

    def __init__(self, notify: StepCallback) -> None:
        self.notify = notify
        self.usage = Usage()
        self.costs: dict[str, float] = dict.fromkeys(STAGES, 0.0)
        self.seq = 0
        self.tool_uses: list[ToolUse] = []
        self.branches: list[BranchSummary] = []
        self.verification: list[ClaimVerdict] = []
        self.metrics = AnswerMetrics()
        self.draft: AnswerDraft | None = None

    @property
    def spent(self) -> float:
        return sum(self.costs.values())

    def add(self, stage: str, usage: Usage, cost_usd: float) -> None:
        self.usage = self.usage + usage
        self.costs[stage] += cost_usd

    async def emit(self, event: StepEvent) -> None:
        """Pass an event on with its place in the whole answer."""
        self.seq += 1
        await self.notify(event.model_copy(update={"seq": self.seq}))

    async def stage(
        self, kind: StepKind, result: LLMResult, span_id: str | None = None, summary: str = ""
    ) -> None:
        await self.emit(
            StepEvent(
                seq=0,
                step=1,
                kind=kind,
                tool=kind,
                arguments=summary,
                model=result.model,
                tokens_in=result.usage.total_input_tokens,
                tokens_out=result.usage.output_tokens,
                cost_usd=result.cost_usd,
                span_id=span_id,
            )
        )

    def result(self, **fields: Any) -> AnswerResult:
        return AnswerResult(
            usage=self.usage,
            cost_usd=self.spent,
            metrics=self.metrics,
            draft=self.draft,
            tool_uses=self.tool_uses,
            branches=self.branches,
            verification=self.verification,
            stage_costs={stage: cost for stage, cost in self.costs.items() if cost},
            **fields,
        )

    def refused(self, decision: RefusalDecision, text: str) -> AnswerResult:
        return self.result(
            answer_markdown=text,
            claims=[],
            citations=[],
            refused=True,
            refusal_kind=decision.kind,
            refusal_reason=decision.reason,
        )


class MultiAgentAnswerSystem:
    """Plans, explores in parallel, writes, and verifies. Same result type, validator, and
    refusal kinds as the other answering modes."""

    name = "multiagent"

    def __init__(
        self,
        llm: LLM,
        toolbox_for: Callable[[int], Toolbox],
        validator: CitationValidator,
        policy: RefusalPolicy,
        *,
        blobs: BlobSource,
        prices: PriceTable,
        sink: TraceSink | None = None,
        limits: MultiAgentConfig | None = None,
        efforts: dict[str, str | None] | None = None,
        default_budget: Budget | None = None,
    ) -> None:
        self.llm = llm
        self.toolbox_for = toolbox_for
        self.validator = validator
        self.policy = policy
        self.prices = prices
        self.sink = sink
        self.limits = limits or MultiAgentConfig()
        self.efforts = dict(efforts or {})
        # The budget this mode is meant to run with (evaluation runs use it).
        self.default_budget = default_budget
        self.planner = Planner(llm, prices=prices, config=self.limits)
        self.explorers = Explorers(llm, toolbox_for, blobs, prices=prices, config=self.limits)
        self.writer = Writer(llm, prices=prices, config=self.limits)
        self.verifier = Verifier(llm, blobs, prices=prices, config=self.limits)

    def config(self) -> dict[str, Any]:
        """What the evaluation cache keys on: models, limits, prompts, tools, schemas."""
        limits = self.limits
        roles = {limits.plan_role, limits.explore_role, limits.write_role, limits.verify_role}
        return {
            "kind": "multiagent",
            "plan_model": self.llm.model_for(limits.plan_role),
            "explore_model": self.llm.model_for(limits.explore_role),
            "write_model": self.llm.model_for(limits.write_role),
            "verify_model": self.llm.model_for(limits.verify_role),
            "efforts": {role: self.efforts.get(role) for role in sorted(roles)},
            "limits": limits.model_dump(mode="json"),
            "prompts_sha256": digest(
                {
                    "planner": PLANNER_PROMPT,
                    "explorer": branch_prompt(limits.branch_max_steps),
                    "writer": WRITER_PROMPT,
                    "verifier": VERIFIER_PROMPT,
                    "repair": REPAIR_PROMPT,
                }
            ),
            "tools_sha256": digest({"tools": tool_definitions()}),
            "schemas_sha256": digest(
                {
                    "plan": output_schema(PlanDraft),
                    "notes": output_schema(BranchNotes),
                    "draft": answer_draft_schema(),
                    "verdicts": output_schema(VerdictSet),
                    "repairs": output_schema(RepairSet),
                }
            ),
        }

    def reserve(self, budget: Budget) -> float:
        """Money set aside, before anything is explored, for the writer and one verification
        pass (each with its repair attempt), estimated from the evidence budget."""
        limits = self.limits
        tokens = limits.evidence_tokens + PROMPT_OVERHEAD_TOKENS
        write = self.prices.max_cost(
            self.llm.model_for(limits.write_role), tokens, budget.max_output_tokens
        )
        verify = self.prices.max_cost(
            self.llm.model_for(limits.verify_role), tokens, limits.verify_output_tokens
        )
        return REPAIR_FACTOR * (write + verify)

    async def answer(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        *,
        on_status: StatusCallback | None = None,
        on_step: StepCallback | None = None,
        on_plan: PlanCallback | None = None,
    ) -> AnswerResult:
        with (
            start_trace("answer", snapshot_id, sink=self.sink) as trace,
            trace_span("answer", "answer", system=self.name) as root,
        ):
            run = _Run(on_step or _no_step)
            result = await self._run(
                run, snapshot_id, question, budget, on_status or _no_status, on_plan or _no_plan
            )
            root.set(
                refused=result.refused,
                refusal_kind=result.refusal_kind,
                claims=len(result.claims),
                citations=len(result.citations),
                evidence=result.metrics.evidence_count,
                evidence_flagged=result.metrics.evidence_flagged,
                tool_calls=len(result.tool_uses),
                branches=len(result.branches),
                branches_incomplete=sum(1 for b in result.branches if b.status != "complete"),
                claims_unverified=sum(1 for v in result.verification if not v.kept),
                claims_repaired=sum(1 for v in result.verification if v.repaired),
                prefilter_citation_validity=result.metrics.prefilter_citation_validity,
                dropped_claim_rate=result.metrics.dropped_claim_rate,
            )
        return result.model_copy(update={"trace_id": trace.id})

    async def _run(
        self,
        run: _Run,
        snapshot_id: int,
        question: str,
        budget: Budget,
        status: StatusCallback,
        on_plan: PlanCallback,
    ) -> AnswerResult:
        limits = self.limits
        cache = ToolCache()

        # --- planning ---
        await status("planning")
        toolbox = self.toolbox_for(snapshot_id)
        with trace_span("multiagent.context", "retrieval") as span:
            context = await anyio.to_thread.run_sync(
                plan_context, toolbox, question, limits.context_k, cache
            )
            span.set(places=len(context.entries))
        reserve = self.reserve(budget)
        decision = self.policy.budget_check(
            budget, spent_usd=0.0, next_call_usd=self.planner.max_cost(question, context) + reserve
        )
        if decision.refuse:
            return run.refused(decision, BUDGET_TEXT)
        try:
            planned = await self.planner.plan(question, context)
        except SpendCapReached as exc:
            return run.refused(daily_cap_decision(exc), DAILY_CAP_TEXT)
        run.add("planner", planned.result.usage, planned.result.cost_usd)
        if planned.plan is None:
            return run.refused(self.policy.api_check(planned.result), API_REFUSAL_TEXT)
        explore_usd = budget.max_cost_usd - run.spent - reserve
        plan = planned.plan.funded(explore_usd)
        count = len(plan.branches)
        summary = f"{count} branch{'es' if count != 1 else ''}"
        await run.stage("plan", planned.result, summary=summary)
        await on_plan(plan)

        # --- exploring ---
        await status("exploring")
        results = await self.explorers.run(
            snapshot_id, question, plan.branches, on_step=run.emit, cache=cache
        )
        for result in results:
            run.add("explorers", result.usage, result.cost_usd)
            run.tool_uses.extend(result.tool_uses)
        run.branches = [summarize(result) for result in results]
        prepared = self.writer.prepare(question, results)
        merged = prepared.merged
        run.metrics = AnswerMetrics(
            evidence_count=len(merged.evidence), evidence_flagged=merged.flagged
        )
        if not merged.evidence:
            return self._nothing_found(run, results)

        # --- writing ---
        decision = self.policy.budget_check(
            budget,
            spent_usd=run.spent,
            next_call_usd=self.writer.max_cost(prepared, budget.max_output_tokens),
        )
        if decision.refuse:
            return run.refused(decision, BUDGET_TEXT)
        await status("writing")
        try:
            written = await self.writer.write(prepared, max_output_tokens=budget.max_output_tokens)
        except SpendCapReached as exc:
            return run.refused(daily_cap_decision(exc), DAILY_CAP_TEXT)
        run.add("writer", written.result.usage, written.result.cost_usd)
        await run.stage("write", written.result)
        draft = written.draft
        if draft is None:
            return run.refused(self.policy.api_check(written.result), API_REFUSAL_TEXT)
        run.draft = draft
        evidence = written.evidence
        with trace_span("validate", "validation", stage="draft") as span:
            report = await anyio.to_thread.run_sync(
                lambda: self.validator.validate(draft, evidence)
            )
            span.set(
                citations=report.metrics.citations_total,
                valid=report.metrics.citations_valid,
                dropped_claims=report.metrics.claims_dropped,
            )
        # Measured on the writer's draft, before anything was removed; the evidence counts are
        # of everything the branches found.
        run.metrics = report.metrics.model_copy(
            update={"evidence_count": len(merged.evidence), "evidence_flagged": merged.flagged}
        )
        decision = self.policy.post_check(
            report, model_refused=draft.refused, reason=draft.refusal_reason
        )
        if decision.refuse:
            text = draft.answer_markdown if draft.refused and draft.answer_markdown else None
            return run.refused(decision, text or UNVERIFIED_TEXT)

        # --- verifying ---
        decision = self.policy.budget_check(
            budget,
            spent_usd=run.spent,
            next_call_usd=self.verifier.max_cost(report.claims, evidence),
        )
        if decision.refuse:  # a claim is never shown unverified
            return run.refused(decision, BUDGET_TEXT)
        await status("verifying")
        before = run.spent
        extra = [0.0]  # what the verifier's calls have cost so far

        async def on_call(kind: str, result: LLMResult, span_id: str) -> None:
            extra[0] += result.cost_usd
            await run.stage("repair" if kind == "repair" else "verify", result, span_id)

        try:
            verified = await self.verifier.verify(
                report.claims,
                evidence,
                can_spend=lambda cost: before + extra[0] + cost <= budget.max_cost_usd,
                on_call=on_call,
            )
        except SpendCapReached as exc:
            run.add("verifier", Usage(), extra[0])
            return run.refused(daily_cap_decision(exc), DAILY_CAP_TEXT)
        run.add("verifier", verified.usage, verified.cost_usd)
        run.verification = verified.verdicts
        if verified.refusal is not None:
            return run.refused(self.policy.api_check(verified.refusal), API_REFUSAL_TEXT)
        if not verified.claims:
            reason = (
                f"The answer was withheld: none of its {len(report.claims)} claims was "
                "supported by the code it cited."
            )
            decision = RefusalDecision(refuse=True, kind="evidence", reason=reason)
            return run.refused(decision, NOT_VERIFIED_TEXT)

        # --- validating ---
        await status("validating")
        checked = draft.model_copy(
            update={"answer_markdown": report.answer_markdown, "claims": report.claims}
        )
        final = verified_draft(checked, verified)
        with trace_span("validate", "validation", stage="verified") as span:
            last = await anyio.to_thread.run_sync(lambda: self.validator.validate(final, evidence))
            span.set(
                citations=last.metrics.citations_total,
                valid=last.metrics.citations_valid,
                dropped_claims=last.metrics.claims_dropped,
            )
        decision = self.policy.post_check(last)
        if decision.refuse:
            return run.refused(decision, UNVERIFIED_TEXT)
        gaps = gaps_note(results)
        text = f"{last.answer_markdown}\n\n{gaps}" if gaps else last.answer_markdown
        return run.result(
            answer_markdown=text, claims=last.claims, citations=last.citations, refused=False
        )

    def _nothing_found(self, run: _Run, results: list[BranchResult]) -> AnswerResult:
        """No branch found any code. Why decides what the answer is."""
        failed = [result for result in results if result.status == "failed"]
        if failed and len(failed) == len(results):
            raised = next((r.exception for r in failed if r.exception is not None), None)
            if raised is not None:
                raise raised  # every branch failed with an error: the answer failed
            decision = RefusalDecision(
                refuse=True, kind="api", reason="The model declined the request."
            )
            return run.refused(decision, API_REFUSAL_TEXT)
        if any(result.error == "daily_cap" for result in results):
            decision = RefusalDecision(
                refuse=True,
                kind="budget",
                reason="The server's daily spending limit stopped the exploration.",
            )
            return run.refused(decision, DAILY_CAP_TEXT)
        if any(result.status in ("partial", "timeout") for result in results):
            decision = RefusalDecision(
                refuse=True,
                kind="budget",
                reason="The exploration reached its limits before any evidence was found.",
            )
            return run.refused(decision, STOPPED_EARLY_TEXT)
        decision = RefusalDecision(
            refuse=True,
            kind="evidence",
            reason="The tools found no code in the snapshot for this question.",
        )
        return run.refused(decision, NO_EVIDENCE_TEXT)
