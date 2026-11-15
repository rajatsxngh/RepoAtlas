"""Prompt-cache breakpoints (PRF-5, decision 0024): where a request carries ``cache_control``.

The static prefix gets two (the last tool definition and the last system block). A call that
may use tools gets a third on the last block of its last message, on the request's copy only,
so the next step of the conversation reads this one from the cache. Calls whose tail is never
sent again get none there. Whether the API reports cache reads is a live-pass item; these tests
pin the request side.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import BaseModel

from repoatlas.config import Settings
from repoatlas.llm.client import LLMClient
from repoatlas.llm.compat import (
    MAX_CACHE_BREAKPOINTS,
    build_request,
    count_cache_breakpoints,
)
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import Message

pytestmark = pytest.mark.anyio

EPHEMERAL = {"type": "ephemeral"}
TOOLS: list[dict[str, Any]] = [
    {"name": "search_code", "description": "Search.", "input_schema": {"type": "object"}},
    {"name": "read_file", "description": "Read.", "input_schema": {"type": "object"}},
]
SYSTEM: Message = {"role": "system", "content": "You answer questions about a repository."}


def conversation(steps: int) -> list[Message]:
    """A system prompt, a question, and ``steps`` finished tool calls."""
    messages: list[Message] = [SYSTEM, {"role": "user", "content": "Where is the total rounded?"}]
    for step in range(1, steps + 1):
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": f"sig{step}"},
                    {"type": "tool_use", "id": f"t{step}", "name": "search_code", "input": {}},
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t{step}", "content": f"result {step}"}
                ],
            }
        )
    return messages


def marked(request: dict[str, Any]) -> dict[str, list[Any]]:
    """Where a request carries breakpoints: tool names, system block indexes, and (message
    index, block index) pairs."""
    return {
        "tools": [tool["name"] for tool in request.get("tools", []) if "cache_control" in tool],
        "system": [
            index
            for index, block in enumerate(request.get("system", []))
            if "cache_control" in block
        ],
        "messages": [
            (m, b)
            for m, message in enumerate(request["messages"])
            if isinstance(message["content"], list)
            for b, block in enumerate(message["content"])
            if "cache_control" in block
        ],
    }


def stripped(value: Any) -> Any:
    """``value`` without any ``cache_control``."""
    if isinstance(value, dict):
        return {key: stripped(item) for key, item in value.items() if key != "cache_control"}
    if isinstance(value, list):
        return [stripped(item) for item in value]
    return value


# --- build_request ----------------------------------------------------------------------------


def test_the_static_prefix_gets_a_breakpoint_on_the_last_tool_and_the_last_system_block() -> None:
    messages = [SYSTEM, {"role": "system", "content": "More rules."}, *conversation(0)[1:]]
    request = build_request("m", messages, max_tokens=10, tools=TOOLS, tool_choice="auto")
    assert marked(request) == {"tools": ["read_file"], "system": [1], "messages": []}
    assert request["tools"][-1]["cache_control"] == EPHEMERAL
    assert request["system"][-1]["cache_control"] == EPHEMERAL
    assert count_cache_breakpoints(request) == 2


def test_without_tools_only_the_system_block_is_marked() -> None:
    request = build_request("m", conversation(0), max_tokens=10)
    assert marked(request) == {"tools": [], "system": [0], "messages": []}


def test_cache_static_off_marks_nothing() -> None:
    request = build_request(
        "m", conversation(2), max_tokens=10, tools=TOOLS, tool_choice="auto", cache_static=False
    )
    assert count_cache_breakpoints(request) == 0


def test_the_conversation_breakpoint_goes_on_the_last_block_of_the_last_message() -> None:
    request = build_request(
        "m",
        conversation(2),
        max_tokens=10,
        tools=TOOLS,
        tool_choice="auto",
        cache_conversation=True,
    )
    last = len(request["messages"]) - 1
    assert marked(request) == {"tools": ["read_file"], "system": [0], "messages": [(last, 0)]}
    assert request["messages"][last]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t2",
        "content": "result 2",
        "cache_control": EPHEMERAL,
    }
    assert count_cache_breakpoints(request) == 3 <= MAX_CACHE_BREAKPOINTS


def test_a_text_question_becomes_one_marked_text_block() -> None:
    request = build_request(
        "m",
        conversation(0),
        max_tokens=10,
        tools=TOOLS,
        tool_choice="auto",
        cache_conversation=True,
    )
    assert request["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Where is the total rounded?",
                    "cache_control": EPHEMERAL,
                }
            ],
        }
    ]


def test_parallel_tool_results_are_marked_once_on_the_last() -> None:
    messages = conversation(1)
    messages[-1]["content"].append(
        {"type": "tool_result", "tool_use_id": "t1b", "content": "another"}
    )
    request = build_request(
        "m", messages, max_tokens=10, tools=TOOLS, tool_choice="auto", cache_conversation=True
    )
    assert marked(request)["messages"] == [(2, 1)]


def test_the_callers_messages_and_tools_are_never_changed() -> None:
    messages, tools = conversation(2), copy.deepcopy(TOOLS)
    before = copy.deepcopy(messages)
    build_request(
        "m", messages, max_tokens=10, tools=tools, tool_choice="auto", cache_conversation=True
    )
    assert messages == before
    assert tools == TOOLS


def test_the_breakpoint_moves_and_the_conversation_stays_append_only() -> None:
    """Step by step, each request marks only its own last message, and apart from the markers
    it starts with exactly the request before it: the prefix the cache matches on."""
    previous: dict[str, Any] | None = None
    for steps in range(4):
        request = build_request(
            "m",
            conversation(steps),
            max_tokens=10,
            tools=TOOLS,
            tool_choice="auto",
            cache_conversation=True,
        )
        assert marked(request)["messages"] == [(len(request["messages"]) - 1, 0)]
        if previous is not None:
            earlier = stripped(previous["messages"])
            assert stripped(request["messages"])[: len(earlier)] == earlier
            assert stripped(request["tools"]) == stripped(previous["tools"])
            assert request["system"] == previous["system"]
        previous = request


def test_a_last_message_that_cannot_carry_a_breakpoint_is_left_alone() -> None:
    assistant_last = [*conversation(0), {"role": "assistant", "content": "Partly."}]
    request = build_request("m", assistant_last, max_tokens=10, cache_conversation=True)
    assert marked(request)["messages"] == []
    # Every message has the block form, so later requests resend the same bytes.
    assert request["messages"][1]["content"] == [{"type": "text", "text": "Partly."}]
    empty = [SYSTEM, {"role": "user", "content": ""}]
    assert (
        marked(build_request("m", empty, max_tokens=10, cache_conversation=True))["messages"] == []
    )
    odd = [SYSTEM, {"role": "user", "content": [{"type": "thinking", "thinking": ""}]}]
    assert marked(build_request("m", odd, max_tokens=10, cache_conversation=True))["messages"] == []


# --- LLMClient --------------------------------------------------------------------------------


class Recorder:
    """A transport that keeps every request and answers with a tool call or with text."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "content": [{"type": "text", "text": '{"answer": "x"}'}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "model": request["model"],
        }


class Reply(BaseModel):
    answer: str


def client(transport: Recorder, **options: Any) -> LLMClient:
    return LLMClient(
        transport,
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        prices=PriceTable(DEFAULT_PRICES),
        **options,
    )


async def test_a_call_that_may_use_tools_marks_its_conversation() -> None:
    transport = Recorder()
    await client(transport).complete("small", conversation(1), tools=TOOLS, max_tokens=100)
    assert marked(transport.requests[0]) == {
        "tools": ["read_file"],
        "system": [0],
        "messages": [(2, 0)],
    }


async def test_calls_whose_conversation_ends_here_do_not() -> None:
    transport = Recorder()
    llm = client(transport)
    # A one-shot answer: its evidence is never sent again.
    await llm.complete("large", conversation(0), schema=Reply, max_tokens=100)
    # A final or notes call: tools present so the prefix matches, but none may be called.
    await llm.complete(
        "large", conversation(2), schema=Reply, tools=TOOLS, tool_choice="none", max_tokens=100
    )
    assert [marked(request)["messages"] for request in transport.requests] == [[], []]
    assert [marked(request)["system"] for request in transport.requests] == [[0], [0]]
    assert marked(transport.requests[1])["tools"] == ["read_file"]


async def test_a_repair_turn_is_not_marked() -> None:
    class BadThenGood(Recorder):
        async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
            response = await super().send(request, timeout_s=timeout_s)
            if len(self.requests) == 1:
                response["content"] = [{"type": "text", "text": "not json"}]
            return response

    transport = BadThenGood()
    await client(transport).complete("large", conversation(0), schema=Reply, max_tokens=100)
    assert len(transport.requests) == 2  # the reply, then the repair
    assert all(marked(request)["messages"] == [] for request in transport.requests)


async def test_the_conversation_breakpoint_can_be_switched_off() -> None:
    transport = Recorder()
    llm = client(transport, cache_conversation=False)
    await llm.complete("small", conversation(1), tools=TOOLS, max_tokens=100)
    assert marked(transport.requests[0]) == {"tools": ["read_file"], "system": [0], "messages": []}

    settings = Settings(_env_file=None, llm_cache_conversation=False, llm_transport="offline")
    assert LLMClient.from_settings(settings).cache_conversation is False
    assert LLMClient.from_settings(
        Settings(_env_file=None, llm_transport="offline")
    ).cache_conversation


async def test_cache_static_off_also_leaves_the_conversation_unmarked() -> None:
    transport = Recorder()
    await client(transport).complete(
        "small", conversation(1), tools=TOOLS, max_tokens=100, cache_static=False
    )
    assert count_cache_breakpoints(transport.requests[0]) == 0
