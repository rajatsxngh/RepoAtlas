"""Scripted models for multi-agent tests: one script per branch, and stage scripts by schema."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, cast

from repoatlas.agent.tools import Toolbox
from repoatlas.llm.fake import FakeCall, FakeLLM, Script
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.multiagent.explorers import Explorers
from repoatlas.multiagent.models import Branch, BranchBudget, MultiAgentConfig
from tests.unit.agent.helpers import FakeToolbox
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


def by_branch(scripts: dict[str, list[Script]]) -> Callable[[FakeCall], Script]:
    """A model that plays each branch from its own script: the item at the index of the
    branch's turn (the notes call comes after the exploration turns)."""

    def pick(call: FakeCall) -> Script:
        branch = branch_of(call)
        assert branch is not None, "not an explorer's call"
        script = scripts[branch]
        index = turns(call)
        assert index < len(script), f"branch {branch} has no scripted result for turn {index + 1}"
        return script[index]

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
