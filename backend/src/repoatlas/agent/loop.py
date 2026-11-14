"""The agent loop (AGT-3, AGT-4): explore one snapshot with tools, inside hard limits.

``Explorer.run`` drives the exploration half of an agent answer. Each step is one call to the
``small`` model with the tools; the model's turn is appended exactly as returned (thinking
blocks included), every tool it called is run, and one user turn with all the results is
appended. Messages are append-only: a result is trimmed and marked *before* it is appended
(``repoatlas.agent.guard``), and nothing already in the conversation is edited or reordered.

Exploration stops when

- the model calls ``final_answer`` (other tools called in the same turn still run);
- the model replies without calling a tool;
- ``max_steps`` steps were made;
- the token budget (input tokens summed over every call of the answer) or the answer's cost
  budget has no room for another step *and* the final answer after it;
- the deployment's daily spend cap stops a call, or the model stops with a refusal.

When exploration stops, the last user turn ends with a closing instruction, so the conversation
is ready for the final answer call as it stands.

Guards:

- **Repeated calls.** The same tool with the same arguments twice in a row is not run again:
  the model gets a note and the earlier result's evidence list.
- **Tool errors** (invalid arguments, an unknown symbol, a path outside the snapshot) go back
  to the model as error results; the step counts, and the loop goes on.
- **Cut-off turns.** If the model's turn hit its output limit, its tool calls may be
  incomplete, so none of them run.

Every step and every tool call is a span of the current trace, and ``on_step`` hears a
``StepEvent`` after each tool call.

``AgentAnswerSystem`` (AGT-5) is the whole answering mode, registered as ``agent``. After
exploring, it gives the ``large`` model the same conversation, unchanged, with tool choice
``none`` and the ``AnswerDraft`` schema; the draft may cite only ledger IDs, and
``CitationValidator`` checks them exactly as it checks one-shot citations. Refusals:

- ``api``: the model stopped with a refusal stop reason, while exploring or answering;
- ``budget``: a limit stopped the exploration before any evidence was found, the final call
  would not fit the answer's cost budget, or the daily spend cap stopped a call. When a limit
  stops the exploration *with* evidence in the ledger, the answer is written from the ledger;
- ``evidence``: the tools found nothing, the model said the evidence isn't enough, or no claim
  kept a valid citation.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field

from repoatlas.agent.guard import (
    EVIDENCE_HEADING,
    MIN_RESULT_TOKENS,
    Prepared,
    prepare_error,
    prepare_result,
)
from repoatlas.agent.ledger import Ledger
from repoatlas.agent.prompting import (
    BUDGET_STOPS,
    FINAL_ANSWER,
    FINAL_ANSWER_TOOL,
    StopReason,
    build_agent_messages,
    closing_text,
    estimate_conversation_tokens,
    system_prompt,
)
from repoatlas.agent.results import ToolError
from repoatlas.agent.tools import TOOLS_BY_NAME, Toolbox, tool_definitions
from repoatlas.answers.policy import RefusalDecision, RefusalPolicy
from repoatlas.answers.prompting import estimate_tokens
from repoatlas.answers.schema import (
    AnswerDraft,
    AnswerMetrics,
    AnswerResult,
    Budget,
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
from repoatlas.canonical import canonical_json, digest
from repoatlas.llm.client import LLM
from repoatlas.llm.pricing import PriceTable
from repoatlas.llm.types import LLMResult, Message, Role, SpendCapReached, ToolCall, Usage
from repoatlas.tracing import TraceSink, start_trace, trace_span

REPAIR_FACTOR = 2  # the final call may be repeated once to repair invalid output
MAX_ARGUMENT_CHARS = 2_000  # arguments kept on a ToolUse; larger ones are dropped
MAX_SUMMARY_CHARS = 200
REPEAT_NOTE = (
    "You made this same call just before; it was not run again and its result has not changed. "
    "Use a different tool or different arguments, or call final_answer."
)
CUT_OFF_NOTE = (
    "Your turn reached its output limit before this call was complete, so nothing ran. Make "
    "the call again, on its own."
)
ENDED_NOTE = "The exploration has ended."


class AgentConfig(BaseModel):
    """Limits of one agent answer (defaults: decision 0022)."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=12, ge=1, le=50)
    max_input_tokens: int = Field(default=200_000, ge=1_000)  # summed over every call
    per_tool_result_tokens: int = Field(default=2_000, ge=MIN_RESULT_TOKENS)
    step_output_tokens: int = Field(default=2_048, ge=256)  # one exploration turn
    explore_role: Role = "small"
    answer_role: Role = "large"


class StepEvent(BaseModel):
    """One thing the agent did, as the ask stream reports it. ``kind`` is ``tool`` for a tool
    call and ``answer`` for the final answer call. The tokens and cost are those of the model
    call that asked for it; when one model turn called several tools they are on the first."""

    model_config = ConfigDict(frozen=True)

    seq: int  # order within the answer, from 1
    step: int  # the model turn, from 1
    kind: Literal["tool", "answer"] = "tool"
    tool: str
    arguments: str = ""  # a short summary
    result_tokens: int = 0
    evidence: int = 0  # evidence entries the result listed
    error: str | None = None
    allowed: bool = True
    cached: bool = False  # a repeated call that was not run again
    truncated: bool = False
    flags: list[str] = Field(default_factory=list)
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    span_id: str | None = None


StepCallback = Callable[[StepEvent], Awaitable[None]]


@dataclass
class Exploration:
    """What exploring left behind: the conversation (ready for the final call unless ``stop``
    is ``refusal`` or ``daily_cap``), the ledger, and what was spent."""

    messages: list[Message]
    tools: list[dict[str, Any]]
    ledger: Ledger
    stop: StopReason
    steps: int = 0
    tool_uses: list[ToolUse] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    events: int = 0
    refusal: LLMResult | None = None
    cap_error: SpendCapReached | None = None


def summarize_arguments(arguments: dict[str, Any]) -> str:
    """``query="cart total", k=3``: short enough for a list of steps."""
    parts = []
    for name, value in arguments.items():
        text = json.dumps(value, ensure_ascii=False)
        parts.append(f"{name}={text if len(text) <= 80 else text[:77] + '...'}")
    summary = ", ".join(parts)
    return (
        summary if len(summary) <= MAX_SUMMARY_CHARS else summary[: MAX_SUMMARY_CHARS - 3] + "..."
    )


def kept_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """The arguments as recorded on a ``ToolUse`` (oversized ones are left out)."""
    if len(json.dumps(arguments, ensure_ascii=False)) > MAX_ARGUMENT_CHARS:
        return {"_dropped": "arguments too large to record"}
    return dict(arguments)


@dataclass
class _Ran:
    """One tool call's outcome: the text for the model and the record of the call."""

    text: str
    is_error: bool
    use: ToolUse
    prepared: Prepared | None = None
    cached: bool = False
    evidence_lines: list[str] = field(default_factory=list)


async def _quiet(_event: StepEvent) -> None:
    return None


class Explorer:
    def __init__(
        self,
        llm: LLM,
        toolbox_for: Callable[[int], Toolbox],
        ledger_for: Callable[[], Ledger],
        *,
        prices: PriceTable,
        config: AgentConfig | None = None,
    ) -> None:
        self.llm = llm
        self.toolbox_for = toolbox_for
        self.ledger_for = ledger_for
        self.prices = prices
        self.config = config or AgentConfig()
        self.tools = [*tool_definitions(), FINAL_ANSWER_TOOL]

    # --- budgets ---------------------------------------------------------------------------

    def final_cost(self, messages: list[Message], budget: Budget) -> float:
        """The most the final answer call can cost on this conversation (with its repair)."""
        tokens = estimate_conversation_tokens(messages, self.tools) + estimate_tokens(
            closing_text("max_steps")
        )
        model = self.llm.model_for(self.config.answer_role)
        return REPAIR_FACTOR * self.prices.max_cost(model, tokens, budget.max_output_tokens)

    def _step_cost(self, messages: list[Message]) -> float:
        tokens = estimate_conversation_tokens(messages, self.tools)
        model = self.llm.model_for(self.config.explore_role)
        return self.prices.max_cost(model, tokens, self.config.step_output_tokens)

    def _room(
        self, messages: list[Message], budget: Budget, usage: Usage, cost: float
    ) -> StopReason | None:
        """Why another step can't be afforded, or ``None`` when it can. A step is affordable
        when it, a result of the largest size, and the final answer after it all fit."""
        tokens = estimate_conversation_tokens(messages, self.tools)
        grown = tokens + self.config.step_output_tokens + self.config.per_tool_result_tokens
        if usage.total_input_tokens + tokens + grown > self.config.max_input_tokens:
            return "token_budget"
        after = self.prices.max_cost(
            self.llm.model_for(self.config.answer_role), grown, budget.max_output_tokens
        )
        if cost + self._step_cost(messages) + REPAIR_FACTOR * after > budget.max_cost_usd:
            return "spend_budget"
        return None

    # --- the loop --------------------------------------------------------------------------

    async def run(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        *,
        on_step: StepCallback | None = None,
    ) -> Exploration:
        config = self.config
        notify = on_step or _quiet
        toolbox = self.toolbox_for(snapshot_id)
        messages = build_agent_messages(question, config.max_steps)
        state = Exploration(
            messages=messages, tools=self.tools, ledger=self.ledger_for(), stop="max_steps"
        )
        previous: tuple[str, str] | None = None  # the call before this one: (tool, arguments)
        previous_lines: list[str] = []
        blocked = self._room(messages, budget, state.usage, state.cost_usd)
        if blocked is not None:
            # Not even one step fits: nothing was explored, so there is nothing to answer from.
            state.stop = blocked
            return state

        while True:
            step = state.steps + 1
            with trace_span("agent.step", "agent", step=step) as span:
                try:
                    result = await self.llm.complete(
                        config.explore_role,
                        messages,
                        tools=self.tools,
                        max_tokens=config.step_output_tokens,
                    )
                except SpendCapReached as exc:
                    state.stop, state.cap_error = "daily_cap", exc
                    span.set(stop="daily_cap")
                    return state
                state.steps = step
                state.usage = state.usage + result.usage
                state.cost_usd += result.cost_usd
                if result.refused:
                    state.stop, state.refusal = "refusal", result
                    span.set(stop="refusal")
                    return state
                messages.append(result.assistant_message)
                calls = result.tool_calls
                span.set(tool_calls=len(calls), stop_reason=result.stop_reason)
                if not calls:
                    state.stop = "no_tool_call"
                    messages.append(_user([_text(closing_text("no_tool_call"))]))
                    return state

                cut_off = result.stop_reason == "max_tokens"
                blocks: list[dict[str, Any]] = []
                finished = False
                first = True
                for call in calls:
                    if call.name == FINAL_ANSWER:
                        finished = True
                        blocks.append(_tool_result(call, ENDED_NOTE, is_error=False))
                        continue
                    key = (call.name, canonical_json(call.input))
                    with trace_span(
                        f"tool.{call.name if call.name in TOOLS_BY_NAME else 'unknown'}",
                        "tool",
                        step=step,
                        arguments=summarize_arguments(call.input),
                    ) as tool_span:
                        if cut_off:
                            ran = _not_run(call, step, CUT_OFF_NOTE)
                        elif key == previous:
                            ran = _repeat(call, step, previous_lines)
                        else:
                            ran = await anyio.to_thread.run_sync(
                                self._run_tool, toolbox, state.ledger, call, step
                            )
                        tool_span.set(
                            result_tokens=ran.use.result_tokens,
                            evidence=len(ran.use.evidence_ids),
                            error=ran.use.error,
                            allowed=ran.use.allowed,
                            cached=ran.cached,
                        )
                    if not cut_off:
                        previous, previous_lines = key, ran.evidence_lines
                    state.tool_uses.append(ran.use)
                    blocks.append(_tool_result(call, ran.text, is_error=ran.is_error))
                    state.events += 1
                    await notify(
                        _event(state.events, ran, result if first else None, str(tool_span.id))
                    )
                    first = False

                stop: StopReason | None = "final_answer" if finished else None
                if stop is None and step >= config.max_steps:
                    stop = "max_steps"
                if stop is None:
                    # The results are not appended yet; count them as they will be.
                    stop = self._room(
                        [*messages, _user(blocks)], budget, state.usage, state.cost_usd
                    )
                if stop is not None:
                    blocks.append(_text(closing_text(stop)))
                messages.append(_user(blocks))
                span.set(stop=stop)
            if stop is not None:
                state.stop = stop
                return state

    def _run_tool(self, toolbox: Toolbox, ledger: Ledger, call: ToolCall, step: int) -> _Ran:
        """Run one tool call (in a worker thread) and prepare its result for the model."""
        arguments = kept_arguments(call.input)
        try:
            result = toolbox.call(call.name, call.input)
        except ToolError as exc:
            text = prepare_error(str(exc))
            use = ToolUse(
                step=step,
                name=call.name[:80],
                arguments=arguments,
                allowed=exc.allowed,
                error=str(exc)[:300],
                result_tokens=estimate_tokens(text),
            )
            return _Ran(text=text, is_error=True, use=use)
        entries = ledger.add(result.evidence)
        lines = ledger.lines(entries)
        prepared = prepare_result(result, lines, max_tokens=self.config.per_tool_result_tokens)
        use = ToolUse(
            step=step,
            name=call.name,
            arguments=arguments,
            evidence_ids=[entry.id for entry in entries],
            result_tokens=prepared.tokens,
            truncated=prepared.truncated,
            flags=prepared.flags,
        )
        return _Ran(
            text=prepared.text, is_error=False, use=use, prepared=prepared, evidence_lines=lines
        )


def _repeat(call: ToolCall, step: int, lines: list[str]) -> _Ran:
    """The answer to a call identical to the one before it: a note and the earlier result's
    evidence list, without running the tool."""
    text = (
        "\n\n".join([REPEAT_NOTE, "\n".join([EVIDENCE_HEADING, *lines])]) if lines else REPEAT_NOTE
    )
    use = ToolUse(
        step=step,
        name=call.name[:80],
        arguments=kept_arguments(call.input),
        result_tokens=estimate_tokens(text),
        cached=True,
    )
    return _Ran(text=text, is_error=False, use=use, cached=True, evidence_lines=lines)


def _not_run(call: ToolCall, step: int, note: str) -> _Ran:
    use = ToolUse(
        step=step,
        name=call.name[:80],
        arguments=kept_arguments(call.input),
        error=note[:300],
        result_tokens=estimate_tokens(note),
    )
    return _Ran(text=note, is_error=True, use=use)


def _event(seq: int, ran: _Ran, llm: LLMResult | None, span_id: str) -> StepEvent:
    use = ran.use
    return StepEvent(
        seq=seq,
        step=use.step,
        tool=use.name,
        arguments=summarize_arguments(use.arguments),
        result_tokens=use.result_tokens,
        evidence=len(use.evidence_ids),
        error=use.error,
        allowed=use.allowed,
        cached=use.cached,
        truncated=use.truncated,
        flags=use.flags,
        model=llm.model if llm is not None else None,
        tokens_in=llm.usage.total_input_tokens if llm is not None else 0,
        tokens_out=llm.usage.output_tokens if llm is not None else 0,
        cost_usd=llm.cost_usd if llm is not None else 0.0,
        span_id=span_id,
    )


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _user(blocks: list[dict[str, Any]]) -> Message:
    return {"role": "user", "content": blocks}


def _tool_result(call: ToolCall, text: str, *, is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call.id, "content": text}
    if is_error:
        block["is_error"] = True
    return block


# ---------------------------------------------------------------------------
# The answering mode
# ---------------------------------------------------------------------------

STEPS = ("exploring", "answering", "validating")
STOPPED_EARLY_TEXT = (
    "The exploration reached its limit before it found any code to answer from. Try a more "
    "specific question."
)
_LIMITS: dict[str, str] = {
    "max_steps": "the step limit",
    "token_budget": "the token budget",
    "spend_budget": "the cost budget",
}


async def _no_status(_step: str) -> None:
    return None


class AgentAnswerSystem:
    """Answers by exploring with tools (``small``) and then writing a cited answer
    (``large``). Same result type, validator, and refusal kinds as one-shot answers."""

    name = "agent"

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
        limits: AgentConfig | None = None,
        efforts: dict[str, str | None] | None = None,
    ) -> None:
        self.llm = llm
        self.validator = validator
        self.policy = policy
        self.prices = prices
        self.sink = sink
        self.limits = limits or AgentConfig()
        self.efforts = dict(efforts or {})
        self.explorer = Explorer(
            llm, toolbox_for, lambda: Ledger(blobs), prices=prices, config=self.limits
        )

    def config(self) -> dict[str, Any]:
        """What the evaluation cache keys on: models, limits, the prompt, the tools."""
        limits = self.limits
        return {
            "kind": "agent",
            "explore_model": self.llm.model_for(limits.explore_role),
            "answer_model": self.llm.model_for(limits.answer_role),
            "explore_effort": self.efforts.get(limits.explore_role),
            "answer_effort": self.efforts.get(limits.answer_role),
            "max_steps": limits.max_steps,
            "max_input_tokens": limits.max_input_tokens,
            "per_tool_result_tokens": limits.per_tool_result_tokens,
            "step_output_tokens": limits.step_output_tokens,
            "prompt_sha256": digest({"prompt": system_prompt(limits.max_steps)}),
            "tools_sha256": digest({"tools": self.explorer.tools}),
            "schema_sha256": digest(answer_draft_schema()),
        }

    async def answer(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        *,
        on_status: StatusCallback | None = None,
        on_step: StepCallback | None = None,
    ) -> AnswerResult:
        with (
            start_trace("answer", snapshot_id, sink=self.sink) as trace,
            trace_span("answer", "answer", system=self.name) as root,
        ):
            result = await self._run(
                snapshot_id, question, budget, on_status or _no_status, on_step or _quiet, root
            )
            root.set(
                refused=result.refused,
                refusal_kind=result.refusal_kind,
                claims=len(result.claims),
                citations=len(result.citations),
                evidence=result.metrics.evidence_count,
                evidence_flagged=result.metrics.evidence_flagged,
                tool_calls=len(result.tool_uses),
                prefilter_citation_validity=result.metrics.prefilter_citation_validity,
                dropped_claim_rate=result.metrics.dropped_claim_rate,
            )
        return result.model_copy(update={"trace_id": trace.id})

    async def _run(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        status: StatusCallback,
        notify: StepCallback,
        root: Any,
    ) -> AnswerResult:
        await status("exploring")
        found = await self.explorer.run(snapshot_id, question, budget, on_step=notify)
        ledger = found.ledger
        root.set(steps=found.steps, stop=found.stop)
        metrics = AnswerMetrics(evidence_count=len(ledger), evidence_flagged=ledger.flagged)

        def refused(
            decision: RefusalDecision,
            text: str,
            *,
            llm: LLMResult | None = None,
            draft: AnswerDraft | None = None,
            measured: AnswerMetrics = metrics,
        ) -> AnswerResult:
            return AnswerResult(
                answer_markdown=text,
                claims=[],
                citations=[],
                refused=True,
                refusal_kind=decision.kind,
                refusal_reason=decision.reason,
                usage=found.usage + llm.usage if llm is not None else found.usage,
                cost_usd=found.cost_usd + (llm.cost_usd if llm is not None else 0.0),
                metrics=measured,
                draft=draft,
                tool_uses=found.tool_uses,
            )

        if found.refusal is not None:
            return refused(self.policy.api_check(found.refusal), API_REFUSAL_TEXT)
        if found.cap_error is not None:
            return refused(daily_cap_decision(found.cap_error), DAILY_CAP_TEXT)
        if len(ledger) == 0:
            if found.stop in BUDGET_STOPS:
                limit = _LIMITS.get(found.stop, "a limit")
                reason = f"The exploration reached {limit} before any evidence was found."
                decision = RefusalDecision(refuse=True, kind="budget", reason=reason)
                return refused(decision, STOPPED_EARLY_TEXT)
            reason = "The tools found no code in the snapshot for this question."
            decision = RefusalDecision(refuse=True, kind="evidence", reason=reason)
            return refused(decision, NO_EVIDENCE_TEXT)

        role = self.limits.answer_role
        decision = self.policy.budget_check(
            budget,
            spent_usd=found.cost_usd,
            next_call_usd=self.explorer.final_cost(found.messages, budget),
        )
        if decision.refuse:
            return refused(decision, BUDGET_TEXT)

        await status("answering")
        with trace_span("agent.answer", "agent", after=found.stop, evidence=len(ledger)) as span:
            try:
                llm = await self.llm.complete(
                    role,
                    found.messages,
                    schema=AnswerDraft,
                    tools=found.tools,
                    tool_choice="none",
                    max_tokens=budget.max_output_tokens,
                )
            except SpendCapReached as exc:
                return refused(daily_cap_decision(exc), DAILY_CAP_TEXT)
        await notify(
            StepEvent(
                seq=found.events + 1,
                step=found.steps + 1,
                kind="answer",
                tool=FINAL_ANSWER,
                model=llm.model,
                tokens_in=llm.usage.total_input_tokens,
                tokens_out=llm.usage.output_tokens,
                cost_usd=llm.cost_usd,
                span_id=str(span.id),
            )
        )
        decision = self.policy.api_check(llm)
        if decision.refuse:
            return refused(decision, API_REFUSAL_TEXT, llm=llm)
        draft = llm.parsed
        if not isinstance(draft, AnswerDraft):
            raise TypeError("The answer call returned no AnswerDraft.")

        await status("validating")
        evidence = ledger.evidence
        with trace_span("validate", "validation") as span:
            report = await anyio.to_thread.run_sync(
                lambda: self.validator.validate(draft, evidence)
            )
            span.set(
                citations=report.metrics.citations_total,
                valid=report.metrics.citations_valid,
                dropped_claims=report.metrics.claims_dropped,
            )
        decision = self.policy.post_check(
            report, model_refused=draft.refused, reason=draft.refusal_reason
        )
        if decision.refuse:
            text = draft.answer_markdown if draft.refused and draft.answer_markdown else None
            return refused(
                decision, text or UNVERIFIED_TEXT, llm=llm, draft=draft, measured=report.metrics
            )
        return AnswerResult(
            answer_markdown=report.answer_markdown,
            claims=report.claims,
            citations=report.citations,
            refused=False,
            usage=found.usage + llm.usage,
            cost_usd=found.cost_usd + llm.cost_usd,
            metrics=report.metrics,
            draft=draft,
            tool_uses=found.tool_uses,
        )
