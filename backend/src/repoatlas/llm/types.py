"""Values every LLM caller sees: roles, usage, tool calls, results, and errors."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["small", "large"]
ROLES: tuple[Role, ...] = ("small", "large")

# A message in Anthropic Messages format: {"role": "user" | "assistant" | "system", "content": ...}.
# Leading system messages become the request's top-level system prompt.
Message = dict[str, Any]


class Usage(BaseModel):
    """Tokens of one call (or a sum of calls). ``input_tokens`` excludes cached tokens."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    input: dict[str, Any]


class LLMResult(BaseModel):
    """One completed call.

    ``content`` is the response's text; ``parsed`` the validated schema object when a schema
    was given. ``blocks`` are the response's content blocks exactly as returned, so a caller
    continuing the conversation appends ``assistant_message`` unchanged (thinking blocks
    included). ``refused`` means the model stopped with a refusal stop reason.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str
    parsed: BaseModel | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    stop_reason: str | None = None
    refused: bool = False
    refusal_category: str | None = None
    thinking_blocks: list[dict[str, Any]] = Field(default_factory=list)
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    latency_ms: float = 0.0
    attempts: int = 1

    @property
    def assistant_message(self) -> Message:
        """The response as the next turn's assistant message (blocks unchanged)."""
        return {"role": "assistant", "content": self.blocks}


class LLMError(Exception):
    """A call failed and was not retried further."""


class LLMRequestError(LLMError):
    """The API rejected the request (4xx other than rate limits); retrying will not help."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SpendCapReached(LLMError):
    """The day's LLM spending has reached the deployment's cap; the call was not made
    (``repoatlas.limits.SpendGuard``)."""

    def __init__(self, spent_usd: float, cap_usd: float) -> None:
        super().__init__(
            f"Today's LLM spending (${spent_usd:.2f}) has reached the daily cap "
            f"(${cap_usd:.2f}); it resets at midnight UTC."
        )
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd


class TransientLLMError(LLMError):
    """Rate limits, overload, server errors, timeouts, and connection failures: retryable."""

    def __init__(
        self, message: str, status: int | None = None, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after_s = retry_after_s


class OutputValidationError(LLMError):
    """The model's structured output failed validation, also after the repair attempt."""

    def __init__(self, message: str, result: LLMResult) -> None:
        super().__init__(message)
        self.result = result
