"""The planner (MAS-1): a question becomes at most four branches with budgets.

One structured call on the ``large`` model. The model proposes branches (a sub-question, seed
symbols, a number of steps); the server decides what is kept:

- at most ``max_branches`` branches, in the model's order, with the IDs ``b1``, ``b2``, …;
- only seed symbols that the context listed (a key the model made up, or copied from an
  instruction, is dropped);
- steps capped per branch (``MIN_BRANCH_STEPS`` to ``branch_max_steps``) and in total
  (``total_steps``, scaled down in proportion when the model asked for more);
- each branch's share of the input-token budget follows its share of the steps;
- a plan with no usable branch becomes one branch with the question itself.

The context comes from one hybrid search for the question (``plan_context``): where the search
found something, never the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape

from repoatlas.agent.loop import ToolCache
from repoatlas.agent.results import ToolError
from repoatlas.agent.tools import Toolbox
from repoatlas.answers.prompting import estimate_message_tokens
from repoatlas.llm.client import LLM
from repoatlas.llm.pricing import PriceTable
from repoatlas.llm.types import LLMResult
from repoatlas.multiagent.models import (
    MAX_SEEDS,
    MAX_SUB_QUESTION_CHARS,
    MIN_BRANCH_STEPS,
    Branch,
    BranchBudget,
    BranchDraft,
    MultiAgentConfig,
    Plan,
    PlanContext,
    PlanDraft,
)
from repoatlas.multiagent.prompting import build_plan_messages
from repoatlas.tracing import trace_span

REPAIR_FACTOR = 2  # the client may repeat a structured call once to repair invalid output


def plan_context(
    toolbox: Toolbox, question: str, k: int, cache: ToolCache | None = None
) -> PlanContext:
    """Where one search for the question finds something. A search that fails (a question the
    search can't take) gives an empty context, and the plan falls back to one branch. With the
    answer's ``cache``, an explorer that makes the same search gets this result."""
    arguments = {"query": question, "k": k}
    try:
        if cache is None:
            result = toolbox.call("search_code", arguments)
        else:
            result, _ = cache.call(toolbox, "search_code", arguments)
    except ToolError:
        return PlanContext()
    return PlanContext.from_refs(result.evidence)


def allot_steps(asked: list[int], config: MultiAgentConfig) -> list[int]:
    """Steps per branch: each within its limits, together within ``total_steps``."""
    steps = [min(max(value, MIN_BRANCH_STEPS), config.branch_max_steps) for value in asked]
    total = sum(steps)
    if total <= config.total_steps:
        return steps
    spare = config.total_steps - MIN_BRANCH_STEPS * len(steps)
    above = [value - MIN_BRANCH_STEPS for value in steps]
    return [MIN_BRANCH_STEPS + spare * extra // sum(above) for extra in above]


def single_branch(question: str, config: MultiAgentConfig) -> Plan:
    """The fallback plan: the question itself, with as many steps as one branch may take."""
    steps = min(config.branch_max_steps, config.total_steps)
    budget = BranchBudget(max_steps=steps, max_input_tokens=config.max_input_tokens)
    return Plan(
        branches=[Branch(id="b1", sub_question=_tidy(question), budget=budget)], fallback=True
    )


def _tidy(text: str) -> str:
    return " ".join(text.split())[:MAX_SUB_QUESTION_CHARS]


def build_plan(
    draft: PlanDraft, question: str, context: PlanContext, config: MultiAgentConfig
) -> Plan:
    """The plan the server keeps of what the model proposed."""
    known = set(context.symbol_keys)
    kept: list[tuple[BranchDraft, str, list[str]]] = []
    for proposed in draft.branches:
        sub_question = _tidy(proposed.sub_question)
        if not sub_question or any(sub_question == other for _, other, _ in kept):
            continue
        # The context shows keys escaped inside an attribute; a model may copy that form.
        seeds = [unescape(key.strip()) for key in proposed.seed_symbols]
        seeds = list(dict.fromkeys(key for key in seeds if key in known))[:MAX_SEEDS]
        kept.append((proposed, sub_question, seeds))
        if len(kept) == config.max_branches:
            break
    if not kept:
        return single_branch(question, config)
    steps = allot_steps([proposed.steps for proposed, _, _ in kept], config)
    total = sum(steps)
    branches = [
        Branch(
            id=f"b{index}",
            sub_question=sub_question,
            seed_symbols=seeds,
            budget=BranchBudget(
                max_steps=allotted,
                max_input_tokens=max(1, config.max_input_tokens * allotted // total),
            ),
        )
        for index, ((_, sub_question, seeds), allotted) in enumerate(
            zip(kept, steps, strict=True), start=1
        )
    ]
    return Plan(branches=branches)


@dataclass
class Planned:
    """A planner call's outcome. ``plan`` is ``None`` only when the model refused."""

    plan: Plan | None
    result: LLMResult


class Planner:
    def __init__(
        self, llm: LLM, *, prices: PriceTable, config: MultiAgentConfig | None = None
    ) -> None:
        self.llm = llm
        self.prices = prices
        self.config = config or MultiAgentConfig()

    def max_cost(self, question: str, context: PlanContext) -> float:
        """The most the planning call can cost (with its repair attempt)."""
        tokens = estimate_message_tokens(build_plan_messages(question, context))
        model = self.llm.model_for(self.config.plan_role)
        return REPAIR_FACTOR * self.prices.max_cost(model, tokens, self.config.plan_output_tokens)

    async def plan(self, question: str, context: PlanContext) -> Planned:
        config = self.config
        with trace_span("multiagent.plan", "agent", context=len(context.entries)) as span:
            result = await self.llm.complete(
                config.plan_role,
                build_plan_messages(question, context),
                schema=PlanDraft,
                max_tokens=config.plan_output_tokens,
            )
            if result.refused:
                span.set(refused=True)
                return Planned(plan=None, result=result)
            draft = result.parsed
            if not isinstance(draft, PlanDraft):
                raise TypeError("The planning call returned no PlanDraft.")
            plan = build_plan(draft, question, context, config)
            span.set(
                proposed=len(draft.branches),
                branches=len(plan.branches),
                steps=plan.steps,
                fallback=plan.fallback,
            )
        return Planned(plan=plan, result=result)
