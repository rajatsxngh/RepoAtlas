"""The compatibility spike's cases (decision 0010 addendum), shared by the recorded-response
unit tests and the live tests that record them.

Each case is a list of request bodies; requests after the first may be built from earlier
responses (``next_request``), always by appending turns. ``check`` asserts what the case
established about the API on the parsed responses (or the error).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from repoatlas.llm.compat import ParsedResponse, build_request, parse_response
from repoatlas.llm.types import LLMError, LLMRequestError

CASSETTES = Path(__file__).parent / "llm_cassettes"
COMPAT = CASSETTES / "compat"
SMALL = "claude-haiku-5-5"
LARGE = "claude-sonnet-5-5"


class Capital(BaseModel):
    country: str
    capital: str
    neighbours: list[str] = Field(max_length=3)


# A static block over the 512-token caching minimum (repeated plain sentences).
STATIC = " ".join(
    f"Rule {n}: answer in one short sentence and never invent facts." for n in range(1, 90)
)

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Current weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
}


@dataclass
class Case:
    name: str
    first: dict[str, Any]
    check: Callable[[list[ParsedResponse], LLMError | None], None]
    next_request: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None
    expect_error: bool = False
    docs_only: bool = False  # can't be triggered on purpose; the cassette follows the docs
    notes: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return COMPAT / f"{self.name}.json"


def _structured(responses: list[ParsedResponse], error: LLMError | None) -> None:
    assert error is None
    (response,) = responses
    assert response.stop_reason == "end_turn"
    parsed = Capital.model_validate_json(response.text)
    assert parsed.capital


def _thinking(responses: list[ParsedResponse], error: LLMError | None) -> None:
    assert error is None
    assert len(responses) == 2
    # The prompt is hard enough that adaptive thinking thinks (at low effort, easy prompts
    # come back without a thinking block, which would prove nothing here).
    assert responses[0].thinking_blocks
    for block in responses[0].thinking_blocks:
        assert block.get("signature") or block["type"] == "redacted_thinking"
    assert responses[1].stop_reason == "end_turn"


def _thinking_next(first: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Turn two appends the first response's blocks unchanged, then a new user turn."""
    return {
        **first,
        "messages": [
            *first["messages"],
            {"role": "assistant", "content": response["content"]},
            {"role": "user", "content": "Now double that number. Reply with just the number."},
        ],
    }


def _cache(responses: list[ParsedResponse], error: LLMError | None) -> None:
    assert error is None
    first, second = responses
    assert first.usage.cache_write_tokens + first.usage.cache_read_tokens > 0
    assert second.usage.cache_read_tokens > 0


def _same(first: dict[str, Any], _response: dict[str, Any]) -> dict[str, Any]:
    return first


def _rejected(responses: list[ParsedResponse], error: LLMError | None) -> None:
    assert not responses
    assert isinstance(error, LLMRequestError)
    assert error.status == 400


def _tool_auto(responses: list[ParsedResponse], error: LLMError | None) -> None:
    assert error is None
    (response,) = responses
    assert response.stop_reason == "tool_use"
    (call,) = response.tool_calls
    assert call.name == "get_weather"
    assert call.input["city"].lower() == "paris"


def _refusal(responses: list[ParsedResponse], error: LLMError | None) -> None:
    assert error is None
    (response,) = responses
    assert response.refused
    assert response.stop_reason == "refusal"


CASES: list[Case] = [
    Case(
        name="structured_output",
        first=build_request(
            SMALL,
            [{"role": "user", "content": "Give France's capital and up to 3 neighbours."}],
            max_tokens=1024,
            schema=Capital,
            effort="low",
            cache_static=False,
        ),
        check=_structured,
        notes=["output_config.format with a flat schema; maxItems stripped, checked by Pydantic"],
    ),
    Case(
        name="thinking_roundtrip",
        first=build_request(
            LARGE,
            [
                {
                    "role": "user",
                    "content": "How many prime numbers are there between 1000 and 1100? "
                    "Reply with just the number.",
                }
            ],
            max_tokens=8192,
            effort="high",
            cache_static=False,
        ),
        next_request=_thinking_next,
        check=_thinking,
        notes=["thinking blocks carry a signature; passed back unchanged on the next turn"],
    ),
    Case(
        name="prompt_cache",
        first=build_request(
            SMALL,
            [
                {"role": "system", "content": STATIC},
                {"role": "user", "content": "Say hello."},
            ],
            max_tokens=256,
            effort="low",
        ),
        next_request=_same,
        check=_cache,
        notes=["breakpoint on the last system block; a second identical call reads the cache"],
    ),
    Case(
        name="forced_tool_choice",
        first={
            "model": LARGE,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "Weather in Paris?"}],
            "tools": [WEATHER_TOOL],
            "tool_choice": {"type": "any"},
        },
        check=_rejected,
        expect_error=True,
        notes=["tool_choice any/tool return 400; build_request refuses them"],
    ),
    Case(
        name="sampling_params",
        first={
            "model": SMALL,
            "max_tokens": 64,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": "Say hi."}],
        },
        check=_rejected,
        expect_error=True,
        notes=["non-default temperature returns 400; build_request never sends it"],
    ),
    Case(
        name="tool_choice_auto",
        first=build_request(
            SMALL,
            [{"role": "user", "content": "Use the get_weather tool for Paris."}],
            max_tokens=512,
            tools=[WEATHER_TOOL],
            tool_choice="auto",
            effort="low",
            cache_static=False,
        ),
        check=_tool_auto,
        notes=["auto + naming the tool in the prompt gets a tool_use block"],
    ),
    Case(
        name="refusal",
        first=build_request(
            LARGE,
            [{"role": "user", "content": "<a request the safety classifiers decline>"}],
            max_tokens=256,
            cache_static=False,
        ),
        check=_refusal,
        docs_only=True,
        notes=["stop_reason refusal with stop_details; never triggered on purpose"],
    ),
]


async def run_case(case: Case, transport: Any) -> tuple[list[ParsedResponse], LLMError | None]:
    """Send the case's requests through ``transport``; the responses parsed, or the error."""
    responses: list[ParsedResponse] = []
    request = case.first
    try:
        raw = await transport.send(request, timeout_s=120.0)
        responses.append(parse_response(raw))
        if case.next_request is not None:
            raw = await transport.send(case.next_request(request, raw), timeout_s=120.0)
            responses.append(parse_response(raw))
    except LLMError as exc:
        return responses, exc
    return responses, None


def by_name(name: str) -> Case:
    return next(case for case in CASES if case.name == name)
