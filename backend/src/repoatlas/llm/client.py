"""``LLMClient``: every LLM call goes through here (ANS-1 to ANS-4, decision 0010).

``complete(role, messages, …)`` routes the role (``small`` or ``large``) to its model, builds
the request with the compatibility rules (``repoatlas.llm.compat``), sends it through a
transport (LiteLLM by default), and returns an ``LLMResult`` with usage, cost, latency, and stop
reason. Transient errors (rate limits, overload, server errors, timeouts) are retried with
exponential backoff, honoring ``retry-after`` when the API gives one; each attempt has a
timeout.

With a ``schema``, the response is parsed and validated with Pydantic. A response that fails
validation gets one repair attempt: the model's reply and a user turn naming the errors are
appended (earlier turns are never edited), and the second reply is validated again. A refusal
is returned as ``refused`` without parsing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from repoatlas.config import Settings
from repoatlas.llm.compat import Effort, build_request, parse_response
from repoatlas.llm.pricing import PriceTable
from repoatlas.llm.transport import LiteLLMTransport, Transport
from repoatlas.llm.types import (
    LLMError,
    LLMResult,
    Message,
    OutputValidationError,
    Role,
    TransientLLMError,
    Usage,
)
from repoatlas.tracing.core import trace_span

REPAIR_PROMPT = (
    "Your previous reply did not match the required JSON schema:\n{errors}\n"
    "Reply again with only the corrected JSON object."
)


class LLM(Protocol):
    """What answer systems need from a client (``LLMClient`` and ``FakeLLM``)."""

    def model_for(self, role: Role) -> str: ...

    async def complete(
        self,
        role: Role,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        cache_static: bool = True,
    ) -> LLMResult: ...


class LLMNotConfigured(LLMError):
    """No API key is set."""


class LLMClient:
    def __init__(
        self,
        transport: Transport,
        *,
        models: dict[Role, str],
        efforts: dict[Role, Effort | None] | None = None,
        prices: PriceTable,
        timeout_s: float = 120.0,
        max_retries: int = 3,
        retry_base_s: float = 1.0,
        retry_max_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.transport = transport
        self.models = dict(models)
        self.efforts = dict(efforts or {})
        self.prices = prices
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.retry_max_s = retry_max_s
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings, transport: Transport | None = None) -> LLMClient:
        """A client for the settings; the LiteLLM transport needs the Anthropic API key."""
        if transport is None:
            if settings.anthropic_api_key is None:
                raise LLMNotConfigured(
                    "Set REPOATLAS_ANTHROPIC_API_KEY in .env to use LLM features."
                )
            transport = LiteLLMTransport(settings.anthropic_api_key)
        return cls(
            transport,
            models={"small": settings.llm_small_model, "large": settings.llm_large_model},
            efforts={"small": settings.llm_small_effort, "large": settings.llm_large_effort},
            prices=PriceTable(settings.llm_prices),
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            retry_base_s=settings.llm_retry_base_s,
            retry_max_s=settings.llm_retry_max_s,
        )

    def model_for(self, role: Role) -> str:
        try:
            return self.models[role]
        except KeyError:
            raise ValueError(f"No model for role {role!r}.") from None

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
        model = self.model_for(role)
        options: dict[str, Any] = {
            "max_tokens": max_tokens,
            "schema": schema,
            "tools": tools,
            "tool_choice": tool_choice if tool_choice is not None else ("auto" if tools else None),
            "effort": self.efforts.get(role),
            "cache_static": cache_static,
        }
        result = await self.send(model, build_request(model, messages, **options), role=role)
        if schema is None or result.refused:
            return result
        error = _validate(schema, result)
        if error is None:
            return result
        # One repair attempt, appended after the model's reply (append-only).
        repair = [
            *messages,
            result.assistant_message,
            {"role": "user", "content": REPAIR_PROMPT.format(errors=error)},
        ]
        second = await self.send(model, build_request(model, repair, **options), role=role)
        combined = _combine(result, second)
        if combined.refused:
            return combined
        error = _validate(schema, combined)
        if error is not None:
            raise OutputValidationError(
                f"The {role} model's output failed validation twice: {error}", combined
            )
        return combined

    async def send(
        self, model: str, request: dict[str, Any], *, role: Role | None = None
    ) -> LLMResult:
        """Send one built request with retries; usage, cost, and latency in the result and on
        an ``llm`` span of the current trace."""
        with trace_span("llm.call", "llm", role=role, requested_model=model) as span:
            result = await self._send(model, request)
            span.record_llm(result)
            return result

    async def _send(self, model: str, request: dict[str, Any]) -> LLMResult:
        attempt = 0
        started = time.perf_counter()
        while True:
            attempt += 1
            try:
                raw = await asyncio.wait_for(
                    self.transport.send(request, timeout_s=self.timeout_s),
                    timeout=self.timeout_s + 5.0,
                )
                break
            except (TransientLLMError, TimeoutError) as exc:
                if attempt > self.max_retries:
                    if isinstance(exc, TimeoutError):
                        raise TransientLLMError(
                            f"The call timed out after {self.timeout_s:g} s."
                        ) from None
                    raise
                await self._sleep(self._delay(attempt, exc))
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        parsed = parse_response(raw)
        served = parsed.model or model
        return LLMResult(
            content=parsed.text,
            tool_calls=parsed.tool_calls,
            usage=parsed.usage,
            cost_usd=self.prices.cost(parsed.usage, model),
            stop_reason=parsed.stop_reason,
            refused=parsed.refused,
            refusal_category=parsed.refusal_category,
            thinking_blocks=parsed.thinking_blocks,
            blocks=parsed.blocks,
            model=served,
            latency_ms=latency_ms,
            attempts=attempt,
        )

    def _delay(self, attempt: int, exc: Exception) -> float:
        retry_after = getattr(exc, "retry_after_s", None)
        if isinstance(retry_after, int | float) and retry_after > 0:
            return min(float(retry_after), self.retry_max_s)
        return min(self.retry_base_s * 2.0 ** (attempt - 1), self.retry_max_s)


def _validate(schema: type[BaseModel], result: LLMResult) -> str | None:
    """Set ``result.parsed``; the validation errors as text when the output doesn't fit."""
    try:
        result.parsed = schema.model_validate_json(result.content)
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors(include_url=False)
        )
    return None


def _combine(first: LLMResult, second: LLMResult) -> LLMResult:
    """The repaired result, with both calls' usage, cost, latency, and attempts."""
    usage: Usage = first.usage + second.usage
    return second.model_copy(
        update={
            "usage": usage,
            "cost_usd": first.cost_usd + second.cost_usd,
            "latency_ms": round(first.latency_ms + second.latency_ms, 2),
            "attempts": first.attempts + second.attempts,
        }
    )
