"""``FakeLLM``: scripted results for tests (ANS-13). Unit tests never call a live LLM.

Give it results in order (``LLMResult``, a JSON-able dict or model for structured calls, a
string for plain text, or an exception to raise), or a function of the call. Every call is
recorded in ``calls``. Results with a schema are parsed like the real client does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import LLMResult, Message, Role, Usage

Script = LLMResult | BaseModel | dict[str, Any] | str | Exception


@dataclass
class FakeCall:
    role: Role
    messages: list[Message]
    schema: type[BaseModel] | None
    tools: list[dict[str, Any]] | None
    max_tokens: int


class FakeLLM:
    def __init__(
        self,
        script: list[Script] | Callable[[FakeCall], Script] | None = None,
        *,
        models: dict[Role, str] | None = None,
        usage: Usage | None = None,
    ) -> None:
        self._script = script if script is not None else []
        self.models: dict[Role, str] = models or {
            "small": "claude-haiku-5-5",
            "large": "claude-sonnet-5-5",
        }
        self.usage = usage or Usage(input_tokens=1000, output_tokens=200)
        self.prices = PriceTable(DEFAULT_PRICES)
        self.calls: list[FakeCall] = []

    def model_for(self, role: Role) -> str:
        return self.models[role]

    async def complete(
        self,
        role: Role,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        cache_static: bool = True,
    ) -> LLMResult:
        call = FakeCall(role, messages, schema, tools, max_tokens)
        self.calls.append(call)
        if callable(self._script):
            item = self._script(call)
        else:
            if len(self.calls) > len(self._script):
                raise AssertionError(f"FakeLLM has no scripted result for call {len(self.calls)}.")
            item = self._script[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResult):
            return item
        model = self.model_for(role)
        if isinstance(item, BaseModel):
            content = item.model_dump_json()
        elif isinstance(item, dict):
            content = json.dumps(item)
        else:
            content = item
        parsed = schema.model_validate_json(content) if schema is not None else None
        return LLMResult(
            content=content,
            parsed=parsed,
            usage=self.usage,
            cost_usd=self.prices.cost(self.usage, model),
            stop_reason="end_turn",
            blocks=[{"type": "text", "text": content}],
            model=model,
            latency_ms=1.0,
        )


def refusal(model: str = "claude-sonnet-5-5", category: str | None = "cyber") -> LLMResult:
    """A scripted API refusal."""
    return LLMResult(
        content="",
        stop_reason="refusal",
        refused=True,
        refusal_category=category,
        model=model,
        usage=Usage(input_tokens=100),
    )
