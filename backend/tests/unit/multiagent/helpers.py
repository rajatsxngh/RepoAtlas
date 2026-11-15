"""Scripted models for multi-agent tests: one script per branch, and stage scripts by schema."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import anyio
from pydantic import BaseModel

from repoatlas.agent.tools import Toolbox
from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import AnswerDraft
from repoatlas.answers.validate import CitationValidator
from repoatlas.llm.fake import FakeCall, FakeLLM, Script
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import LLMResult, Message, Role
from repoatlas.multiagent.explorers import Explorers
from repoatlas.multiagent.models import Branch, BranchBudget, MultiAgentConfig, PlanDraft
from repoatlas.multiagent.system import MultiAgentAnswerSystem
from repoatlas.multiagent.verifier import RepairSet, VerdictSet
from tests.unit.agent.helpers import FakeToolbox, Sink
from tests.unit.answers.helpers import FakeBlobs

QUESTION = "How is the app created, and what does slugify do?"
_BRANCH = re.compile(r'<branch id="(b\d+)">')


def branch_of(call: FakeCall) -> str | None:
    """The branch a call belongs to (``None`` for a call that is not an explorer's)."""
    content: Any = call.messages[1]["content"] if len(call.messages) > 1 else ""
    if isinstance(content, str):
        return None
    for block in content:
        found = _BRANCH.search(block.get("text", "")) if block.get("type") == "text" else None
        if found:
            return found[1]
    return None


def turns(call: FakeCall) -> int:
    return sum(1 for message in call.messages if message["role"] == "assistant")


def by_branch(scripts: Mapping[str, Sequence[Any]]) -> Callable[[FakeCall], Script]:
    """A model that plays each branch from its own script: the item at the index of the
    branch's turn (the notes call comes after the exploration turns)."""

    def pick(call: FakeCall) -> Script:
        branch = branch_of(call)
        assert branch is not None, "not an explorer's call"
        script = scripts[branch]
        index = turns(call)
        assert index < len(script), f"branch {branch} has no scripted result for turn {index + 1}"
        item: Script = script[index]
        return item

    return pick


def branch(
    branch_id: str,
    sub_question: str,
    *seeds: str,
    steps: int = 4,
    tokens: int = 100_000,
    cost: float = 0.02,
) -> Branch:
    return Branch(
        id=branch_id,
        sub_question=sub_question,
        seed_symbols=list(seeds),
        budget=BranchBudget(max_steps=steps, max_input_tokens=tokens, max_cost_usd=cost),
    )


def notes(*findings: tuple[str, list[str]], unanswered: str | None = None) -> dict[str, Any]:
    return {
        "findings": [{"text": text, "evidence_ids": ids} for text, ids in findings],
        "unanswered": unanswered,
    }


def explorers(
    script: Any, toolbox: FakeToolbox | None = None, llm: FakeLLM | None = None, **config: Any
) -> tuple[Explorers, FakeLLM, FakeToolbox]:
    model = llm or FakeLLM(script)
    box = toolbox or FakeToolbox()
    made = Explorers(
        model,
        lambda snapshot_id: cast(Toolbox, box),
        FakeBlobs(),
        prices=PriceTable(DEFAULT_PRICES),
        config=MultiAgentConfig(**config),
    )
    return made, model, box


# ---------------------------------------------------------------------------
# The whole pipeline
# ---------------------------------------------------------------------------


def plan(*branches: tuple[str, list[str], int]) -> dict[str, Any]:
    """A scripted planner reply: (sub-question, seed symbols, steps) per branch."""
    return {
        "branches": [
            {"sub_question": text, "seed_symbols": seeds, "steps": steps}
            for text, seeds, steps in branches
        ]
    }


def verdicts(**labels: str) -> dict[str, Any]:
    return {
        "verdicts": [
            {"claim_id": claim_id, "label": label, "reason": f"{claim_id} is {label}."}
            for claim_id, label in labels.items()
        ]
    }


def pipeline(
    planned: Any,
    branches: Mapping[str, Sequence[Any]],
    draft: Any = None,
    checks: Sequence[Any] | None = None,
    repair: Any = None,
) -> Callable[[FakeCall], Script]:
    """A model that plays every stage: the planner's reply, each branch's script, the
    writer's draft, the verifier's replies in order, and the repair."""
    explore = by_branch(branches)
    remaining: list[Any] = list(checks or [])

    def pick(call: FakeCall) -> Any:
        if call.schema is PlanDraft:
            return planned
        if branch_of(call) is not None:
            return explore(call)
        if call.schema is AnswerDraft:
            assert draft is not None, "no draft was scripted"
            return draft
        if call.schema is VerdictSet:
            assert remaining, "no further verification reply was scripted"
            return remaining.pop(0)
        if call.schema is RepairSet:
            assert repair is not None, "no repair was scripted"
            return repair
        raise AssertionError(f"unexpected call with schema {call.schema}")

    return pick


class TimedLLM(FakeLLM):
    """A scripted model whose calls can take time: ``delay(call)`` seconds each."""

    def __init__(self, script: Any, delay: Callable[[FakeCall], float]) -> None:
        super().__init__(script)
        self.delay = delay

    async def complete(
        self,
        role: Role,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        cache_static: bool = True,
        tool_choice: str | None = None,
    ) -> LLMResult:
        wait = self.delay(FakeCall(role, messages, schema, tools, max_tokens, tool_choice))
        if wait:
            await anyio.sleep(wait)
        return await super().complete(
            role,
            messages,
            schema=schema,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )


def system(
    script: Any,
    toolbox: FakeToolbox | None = None,
    llm: FakeLLM | None = None,
    **limits: Any,
) -> tuple[MultiAgentAnswerSystem, FakeLLM, FakeToolbox, Sink]:
    model = llm or FakeLLM(script)
    box = toolbox or FakeToolbox()
    blobs = FakeBlobs()
    sink = Sink()
    made = MultiAgentAnswerSystem(
        model,
        lambda snapshot_id: cast(Toolbox, box),
        CitationValidator(blobs),
        RefusalPolicy(),
        blobs=blobs,
        prices=PriceTable(DEFAULT_PRICES),
        sink=sink,
        limits=MultiAgentConfig(**limits),
    )
    return made, model, box, sink
