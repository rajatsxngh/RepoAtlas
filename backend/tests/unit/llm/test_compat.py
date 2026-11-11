"""The compatibility spike's rules (decision 0010 addendum), one test per behavior.

Request rules are checked on ``build_request``; response behavior is checked by replaying the
recorded responses in ``tests/llm_cassettes/compat`` (recorded with ``-m live``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, ValidationError

from repoatlas.llm.cassette import Cassette, CassetteMismatch, CassetteTransport
from repoatlas.llm.compat import (
    SAMPLING_PARAMS,
    UNSUPPORTED_KEYWORDS,
    SchemaError,
    build_request,
    count_cache_breakpoints,
    output_schema,
    parse_response,
    schema_keywords,
)
from tests.llm_compat_cases import (
    CASES,
    COMPAT,
    STATIC,
    WEATHER_TOOL,
    Capital,
    Case,
    by_name,
    run_case,
)

pytestmark = pytest.mark.anyio
USER = [{"role": "user", "content": "Hi"}]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_recorded_behavior(case: Case) -> None:
    transport = CassetteTransport(case.path)
    responses, error = await run_case(case, transport)
    case.check(responses, error)
    assert transport.exhausted


def test_every_case_has_a_cassette_without_secrets() -> None:
    assert {path.stem for path in COMPAT.glob("*.json")} == {case.name for case in CASES}
    for case in CASES:
        raw = case.path.read_text(encoding="utf-8")
        assert "sk-ant-" not in raw
        assert "x-api-key" not in raw.lower()
        Cassette.model_validate_json(raw)


# --- sampling parameters and thinking settings ----------------------------------------------


def test_sampling_parameters_and_thinking_are_never_sent() -> None:
    request = build_request("m", USER, max_tokens=10, effort="low")
    assert not SAMPLING_PARAMS & request.keys()
    assert "thinking" not in request  # adaptive thinking is the default on these models


# --- tool choice ----------------------------------------------------------------------------


@pytest.mark.parametrize("choice", ["any", "tool", "required"])
def test_forced_tool_choice_is_refused_before_sending(choice: str) -> None:
    with pytest.raises(ValueError, match="not accepted"):
        build_request("m", USER, max_tokens=10, tools=[WEATHER_TOOL], tool_choice=choice)


def test_auto_tool_choice_is_sent() -> None:
    request = build_request("m", USER, max_tokens=10, tools=[WEATHER_TOOL], tool_choice="auto")
    assert request["tool_choice"] == {"type": "auto"}
    assert request["tools"] == [WEATHER_TOOL]


# --- thinking -------------------------------------------------------------------------------


def test_thinking_blocks_are_kept_exactly() -> None:
    cassette = CassetteTransport(by_name("thinking_roundtrip").path).cassette
    first, second = cassette.interactions
    assert first.response is not None
    parsed = parse_response(first.response)
    assert parsed.thinking_blocks
    assert parsed.blocks == first.response["content"]
    # The next turn carries the blocks unchanged, after the unchanged first turn.
    replayed = second.request["messages"]
    assert replayed[0] == first.request["messages"][0]
    assert replayed[1] == {"role": "assistant", "content": first.response["content"]}


# --- structured outputs ---------------------------------------------------------------------


class Inner(BaseModel):
    name: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)


class Outer(BaseModel):
    title: str
    items: list[Inner] = Field(max_length=5)
    note: str | None = None


class Node(BaseModel):
    children: list[Node]


def test_schema_is_flat_closed_and_free_of_unsupported_keywords() -> None:
    schema = output_schema(Outer)
    assert not schema_keywords(schema) & (UNSUPPORTED_KEYWORDS | {"$ref", "$defs"})
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "items", "note"]
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["name", "score"]


def test_pydantic_still_enforces_stripped_constraints() -> None:
    with pytest.raises(ValidationError):
        Capital.model_validate({"country": "x", "capital": "y", "neighbours": ["a"] * 4})


def test_recursive_schemas_are_rejected() -> None:
    with pytest.raises(SchemaError, match="recursive"):
        output_schema(Node)


def test_schema_and_effort_go_in_output_config() -> None:
    request = build_request("m", USER, max_tokens=10, schema=Capital, effort="medium")
    assert request["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": output_schema(Capital)},
    }


# --- prompt caching -------------------------------------------------------------------------


def test_breakpoint_on_the_last_system_block() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "rules"},
        {"role": "system", "content": [{"type": "text", "text": STATIC}]},
        *USER,
    ]
    request = build_request("m", messages, max_tokens=10)
    assert request["system"][0] == {"type": "text", "text": "rules"}
    assert request["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert count_cache_breakpoints(request) == 1
    uncached = build_request("m", messages, max_tokens=10, cache_static=False)
    assert count_cache_breakpoints(uncached) == 0


def test_at_most_four_breakpoints() -> None:
    marked = {"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [marked] * 4},
    ]
    with pytest.raises(ValueError, match="at most 4"):
        build_request("m", messages, max_tokens=10)


def test_cache_usage_is_read() -> None:
    cassette = CassetteTransport(by_name("prompt_cache").path).cassette
    response = cassette.interactions[1].response
    assert response is not None
    assert parse_response(response).usage.cache_read_tokens > 0


# --- refusals -------------------------------------------------------------------------------


def test_refusal_stop_reason_is_surfaced() -> None:
    response = CassetteTransport(by_name("refusal").path).cassette.interactions[0].response
    assert response is not None
    parsed = parse_response(response)
    assert parsed.refused
    assert parsed.refusal_category == "cyber"
    assert not parse_response({**response, "stop_reason": "end_turn"}).refused


# --- append-only conversations --------------------------------------------------------------


def test_build_request_never_edits_the_callers_messages() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
    ]
    before = repr(messages)
    request = build_request("m", messages, max_tokens=10)
    request["messages"][0]["content"][0]["text"] = "edited"
    assert repr(messages) == before


def test_the_conversation_must_start_with_the_user() -> None:
    with pytest.raises(ValueError, match="from the user"):
        build_request("m", [{"role": "assistant", "content": "x"}], max_tokens=10)


def test_tool_inputs_are_parsed_as_json() -> None:
    block = {"type": "tool_use", "id": "t", "name": "n", "input": '{"path": "a\\/b"}'}
    (call,) = parse_response({"content": [block]}).tool_calls
    assert call.input == {"path": "a/b"}


# --- cassettes ------------------------------------------------------------------------------


async def test_a_changed_request_fails_the_replay() -> None:
    case = by_name("structured_output")
    transport = CassetteTransport(case.path)
    changed = {**case.first, "max_tokens": case.first["max_tokens"] + 1}
    with pytest.raises(CassetteMismatch, match="differs"):
        await transport.send(changed, timeout_s=1)


async def test_recording_writes_bodies_only(tmp_path: Path) -> None:
    class Echo:
        async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}

    path = tmp_path / "c.json"
    recorder = CassetteTransport(path, mode="record", inner=Echo(), description="d")
    await recorder.send({"model": "m", "messages": USER}, timeout_s=1)
    recorder.save()
    replayed = await CassetteTransport(path).send({"model": "m", "messages": USER}, timeout_s=1)
    assert replayed["stop_reason"] == "end_turn"
