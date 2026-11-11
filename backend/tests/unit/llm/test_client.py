"""``LLMClient``: routing, retries, timeouts, repair, usage and cost; ``FakeLLM``; pricing."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from repoatlas.config import Settings
from repoatlas.llm.cassette import CassetteTransport
from repoatlas.llm.client import LLMClient, LLMNotConfigured
from repoatlas.llm.fake import FakeLLM, refusal
from repoatlas.llm.pricing import DEFAULT_PRICES, ModelPrice, PriceTable, UnknownModelPrice, cost
from repoatlas.llm.transport import LiteLLMTransport, error_response
from repoatlas.llm.types import (
    LLMRequestError,
    OutputValidationError,
    TransientLLMError,
    Usage,
)
from tests.llm_compat_cases import Capital, by_name

pytestmark = pytest.mark.anyio
USER = [{"role": "user", "content": "Hi"}]


def message(
    text: str, *, stop: str = "end_turn", usage: tuple[int, int] = (10, 5)
) -> dict[str, Any]:
    return {
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }


class Scripted:
    """A transport returning (or raising) scripted items; records request bodies."""

    def __init__(self, *items: Any, delay_s: float = 0.0) -> None:
        self.items = list(items)
        self.requests: list[dict[str, Any]] = []
        self.delay_s = delay_s

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)


def client(transport: Any, **options: Any) -> tuple[LLMClient, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    llm = LLMClient(
        transport,
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        efforts={"small": "low", "large": "medium"},
        prices=PriceTable(DEFAULT_PRICES),
        sleep=sleep,
        **options,
    )
    return llm, slept


async def test_roles_route_to_models_and_efforts() -> None:
    transport = Scripted(message("a"), message("b"))
    llm, _ = client(transport)
    await llm.complete("small", USER, max_tokens=10)
    await llm.complete("large", USER, max_tokens=10)
    assert [r["model"] for r in transport.requests] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert [r["output_config"]["effort"] for r in transport.requests] == ["low", "medium"]


async def test_usage_cost_and_latency() -> None:
    transport = Scripted(message("ok", usage=(1_000_000, 100_000)))
    llm, _ = client(transport)
    result = await llm.complete("large", USER, max_tokens=10)
    assert result.content == "ok"
    assert result.usage == Usage(input_tokens=1_000_000, output_tokens=100_000)
    assert result.cost_usd == pytest.approx(2.00 + 1.00)
    assert result.latency_ms >= 0
    assert result.stop_reason == "end_turn"
    assert result.attempts == 1


async def test_transient_errors_are_retried_with_backoff() -> None:
    transport = Scripted(
        TransientLLMError("overloaded", status=529),
        TransientLLMError("server", status=500),
        TransientLLMError("slow down", status=429, retry_after_s=7),
        message("ok"),
    )
    llm, slept = client(transport, max_retries=3, retry_base_s=1.0)
    result = await llm.complete("small", USER, max_tokens=10)
    assert result.content == "ok"
    assert result.attempts == 4
    assert slept == [1.0, 2.0, 7.0]  # doubling, then the API's retry-after


async def test_retries_give_up_after_the_limit() -> None:
    transport = Scripted(*[TransientLLMError("busy", status=529) for _ in range(3)])
    llm, slept = client(transport, max_retries=2, retry_max_s=1.5)
    with pytest.raises(TransientLLMError):
        await llm.complete("small", USER, max_tokens=10)
    assert slept == [1.0, 1.5]


async def test_request_errors_are_not_retried() -> None:
    transport = Scripted(LLMRequestError("bad", status=400), message("never"))
    llm, slept = client(transport)
    with pytest.raises(LLMRequestError):
        await llm.complete("small", USER, max_tokens=10)
    assert slept == []
    assert len(transport.requests) == 1


async def test_each_attempt_has_a_timeout() -> None:
    llm, _ = client(Scripted(message("late"), delay_s=1.0), timeout_s=-4.95, max_retries=0)
    with pytest.raises(TransientLLMError, match="timed out"):
        await llm.complete("small", USER, max_tokens=10)


class Answer(BaseModel):
    text: str
    count: int


async def test_structured_output_is_parsed() -> None:
    llm, _ = client(Scripted(message('{"text": "x", "count": 2}')))
    result = await llm.complete("small", USER, schema=Answer, max_tokens=10)
    assert result.parsed == Answer(text="x", count=2)


async def test_one_repair_attempt_appends_to_the_conversation() -> None:
    bad = message('{"text": "x"}', usage=(10, 5))
    good = message('{"text": "x", "count": 3}', usage=(30, 6))
    transport = Scripted(bad, good)
    llm, _ = client(transport)
    result = await llm.complete("small", USER, schema=Answer, max_tokens=10)
    assert result.parsed == Answer(text="x", count=3)
    assert result.usage == Usage(input_tokens=40, output_tokens=11)
    first, second = transport.requests
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert second["messages"][1] == {"role": "assistant", "content": bad["content"]}
    assert "count" in second["messages"][2]["content"]


async def test_a_second_failure_raises_with_the_result() -> None:
    llm, _ = client(Scripted(message("no"), message("still no")))
    with pytest.raises(OutputValidationError) as info:
        await llm.complete("small", USER, schema=Answer, max_tokens=10)
    assert info.value.result.usage.input_tokens == 20


async def test_a_refusal_is_returned_unparsed() -> None:
    response = {**message(""), "stop_reason": "refusal", "stop_details": {"category": "bio"}}
    llm, _ = client(Scripted(response))
    result = await llm.complete("large", USER, schema=Answer, max_tokens=10)
    assert result.refused
    assert result.parsed is None
    assert result.refusal_category == "bio"


async def test_the_client_matches_the_recorded_structured_output() -> None:
    case = by_name("structured_output")
    llm, _ = client(CassetteTransport(case.path))
    result = await llm.complete(
        "small", case.first["messages"], schema=Capital, max_tokens=1024, cache_static=False
    )
    assert isinstance(result.parsed, Capital)
    assert result.thinking_blocks
    assert result.assistant_message["content"] == result.blocks


async def test_tool_calls_use_auto_choice() -> None:
    case = by_name("tool_choice_auto")
    llm, _ = client(CassetteTransport(case.path))
    result = await llm.complete(
        "small",
        case.first["messages"],
        tools=case.first["tools"],
        max_tokens=512,
        cache_static=False,
    )
    assert [call.name for call in result.tool_calls] == ["get_weather"]


def test_from_settings_needs_a_key() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(LLMNotConfigured, match="REPOATLAS_ANTHROPIC_API_KEY"):
        LLMClient.from_settings(settings)
    keyed = Settings(_env_file=None, anthropic_api_key=SecretStr("test"))
    llm = LLMClient.from_settings(keyed)
    assert llm.models == {"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"}
    assert isinstance(llm.transport, LiteLLMTransport)


def test_prices_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REPOATLAS_LLM_PRICES",
        '{"m": {"input": 1, "output": 2, "cache_read": 0.5, "cache_write": 4}}',
    )
    settings = Settings(_env_file=None)
    table = PriceTable(settings.llm_prices)
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert table.cost(usage, "m") == pytest.approx(7.5)
    with pytest.raises(UnknownModelPrice):
        table.cost(usage, "claude-sonnet-5-5")


def test_default_prices() -> None:
    usage = Usage(input_tokens=2000, output_tokens=500, cache_read_tokens=1000)
    assert cost(usage, "claude-sonnet-5-5") == pytest.approx((4000 + 5000 + 100) / 1e6)
    assert DEFAULT_PRICES["claude-haiku-5-5"] == ModelPrice(
        input=0.1, output=0.5, cache_read=0.01, cache_write=0.125
    )
    assert PriceTable(DEFAULT_PRICES).max_cost("claude-haiku-5-5", 1000, 1000) == pytest.approx(
        0.0006
    )


def test_status_codes_map_to_error_kinds() -> None:
    assert isinstance(error_response(529, "x"), TransientLLMError)
    assert isinstance(error_response(429, "x"), TransientLLMError)
    assert isinstance(error_response(400, "x"), LLMRequestError)
    leaked = error_response(401, "bad key sk-ant-api03-abcDEF_123")
    assert "sk-ant" not in str(leaked)


async def test_litellm_transport_forwards_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM's HTTP layer is stubbed: the body reaches the API unchanged, errors keep their
    status, and the key is sent but never returned."""
    from litellm.llms.custom_httpx import http_handler

    sent: list[httpx.Request] = []
    replies = [httpx.Response(200, json=message("hi")), httpx.Response(429, json={})]

    async def post(self: Any, url: str, data: Any = None, headers: Any = None, **_: Any) -> Any:
        request = httpx.Request("POST", url, content=data, headers=headers)
        sent.append(request)
        reply = replies.pop(0)
        response = httpx.Response(reply.status_code, content=reply.content, request=request)
        response.headers["retry-after"] = "4"
        response.raise_for_status()
        return response

    monkeypatch.setattr(http_handler.AsyncHTTPHandler, "post", post)
    transport = LiteLLMTransport(SecretStr("sk-ant-test-key"))
    body = by_name("prompt_cache").first
    response = await transport.send(body, timeout_s=5)
    assert response["content"][0]["text"] == "hi"
    forwarded = json.loads(sent[0].content)
    assert forwarded["system"] == body["system"]
    assert forwarded["output_config"] == body["output_config"]
    assert "temperature" not in forwarded
    assert sent[0].headers["x-api-key"] == "sk-ant-test-key"
    with pytest.raises(TransientLLMError) as info:
        await transport.send(body, timeout_s=5)
    assert info.value.status == 429
    assert info.value.retry_after_s == 4
    assert "sk-ant-test-key" not in str(info.value)


async def test_fake_llm_scripts_and_records() -> None:
    fake = FakeLLM([{"text": "a", "count": 1}, "plain", refusal(), ValueError("boom")])
    first = await fake.complete("large", USER, schema=Answer, max_tokens=5)
    assert first.parsed == Answer(text="a", count=1)
    assert (await fake.complete("small", USER, max_tokens=5)).content == "plain"
    assert (await fake.complete("large", USER, max_tokens=5)).refused
    with pytest.raises(ValueError, match="boom"):
        await fake.complete("large", USER, max_tokens=5)
    assert [call.role for call in fake.calls] == ["large", "small", "large", "large"]
    with pytest.raises(AssertionError, match="no scripted result"):
        await fake.complete("large", USER, max_tokens=5)
