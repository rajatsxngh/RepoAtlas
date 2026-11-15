"""A whole agent conversation replayed through the real ``LLMClient`` (AGT-3, AGT-4, decision
0010): every request the agent sends must still match the cassette, and the answer built from
the recorded responses must validate against the stored blobs.

The cassette is hand-written until the live pass (see ``recorded.py``); what this test pins
down is the request side, which is ours: the shapes a real model will be sent.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest
from sqlalchemy import Engine

from repoatlas.agent.prompting import closing_text, system_prompt
from repoatlas.agent.tools import TOOL_NAMES
from repoatlas.llm.cassette import Cassette
from repoatlas.llm.compat import count_cache_breakpoints
from tests.cassettes import finish, use_cassette
from tests.integration.agent.recorded import (
    AGENT_CASSETTE,
    DOCS_NOTE,
    QUESTION,
    agent_answer_with,
    docs_transport,
    save_docs,
    writing_docs,
)
from tests.integration.retrieval.conftest import store_fixture

pytestmark = pytest.mark.anyio


def recorded() -> Cassette:
    return Cassette.model_validate_json(AGENT_CASSETTE.read_text(encoding="utf-8"))


def blocks(message: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [block for block in message["content"] if block.get("type") == kind]


async def test_the_recorded_agent_conversation_replays_and_validates(
    pg_engine: Engine, clean_store: None
) -> None:
    snapshot_id = store_fixture(pg_engine, "py_small")
    if writing_docs():
        transport = docs_transport()
        result = await agent_answer_with(transport, pg_engine, snapshot_id)
        save_docs(transport)
    else:
        transport = use_cassette(AGENT_CASSETTE)
        result = await agent_answer_with(transport, pg_engine, snapshot_id)
        finish(transport)
    assert not result.refused, result.refusal_reason
    assert [claim.id for claim in result.claims] == ["c1"]
    (citation,) = result.citations
    assert (citation.ref.path, citation.ref.start_line, citation.ref.end_line) == (
        "src/shop/cart.py",
        17,
        19,
    )
    assert [use.name for use in result.tool_uses] == [
        "find_definition",
        "read_file",
        "find_callers",
    ]
    assert all(use.allowed and use.error is None for use in result.tool_uses)
    # Usage and cost are the sums over the four recorded calls.
    assert result.usage.input_tokens == 2890 + 3240 + 3980 + 4210
    assert result.usage.output_tokens == 64 + 118 + 31 + 180
    assert result.cost_usd > 0 and result.trace_id is not None


def test_the_requests_route_models_and_follow_the_request_rules() -> None:
    requests = [interaction.request for interaction in recorded().interactions]
    assert len(requests) == 4
    assert [request["model"] for request in requests] == [
        "claude-haiku-5-5",
        "claude-haiku-5-5",
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
    ]
    for request in requests:
        # The same tools on every call, in a fixed order; never a forced tool choice.
        assert [tool["name"] for tool in request["tools"]] == [*TOOL_NAMES, "final_answer"]
        assert not {"temperature", "top_p", "top_k", "thinking"} & set(request)
        # One system block, the static prompt, carrying the only cache breakpoint.
        (system,) = request["system"]
        assert system["text"] == system_prompt(12)
        assert system["cache_control"] == {"type": "ephemeral"}
        assert count_cache_breakpoints(request) == 1
    explore, final = requests[:3], requests[3]
    assert all(request["tool_choice"] == {"type": "auto"} for request in explore)
    assert all(request["output_config"] == {"effort": "low"} for request in explore)
    assert all(request["max_tokens"] == 2_048 for request in explore)
    assert final["tool_choice"] == {"type": "none"}
    assert final["output_config"]["effort"] == "medium"
    assert final["output_config"]["format"]["type"] == "json_schema"
    assert set(final["output_config"]["format"]["schema"]["properties"]) == {
        "answer_markdown",
        "claims",
        "refused",
        "refusal_reason",
    }


def test_the_conversation_is_append_only_and_turns_go_back_unchanged() -> None:
    cassette = recorded()
    requests = [interaction.request for interaction in cassette.interactions]
    responses = [interaction.response for interaction in cassette.interactions]
    first = requests[0]["messages"]
    assert len(first) == 1 and first[0]["role"] == "user"
    assert first[0]["content"] == [{"type": "text", "text": f"<question>\n{QUESTION}\n</question>"}]
    for earlier, later in itertools.pairwise(requests):
        assert later["messages"][: len(earlier["messages"])] == earlier["messages"]
        assert len(later["messages"]) == len(earlier["messages"]) + 2  # the turn, its results
    for index, response in enumerate(responses[:3]):
        assert response is not None
        turn = requests[index + 1]["messages"][2 * index + 1]
        # The model's turn, block for block: the thinking block keeps its signature.
        assert turn == {"role": "assistant", "content": response["content"]}
        results = requests[index + 1]["messages"][2 * index + 2]
        assert results["role"] == "user"
        ids = [block["tool_use_id"] for block in blocks(results, "tool_result")]
        assert ids == [block["id"] for block in blocks(turn, "tool_use")]
    thinking = requests[1]["messages"][1]["content"][0]
    assert thinking == {"type": "thinking", "thinking": "", "signature": "docs-signature-0001"}
    assert requests[3]["messages"][1]["content"][0] == thinking  # still there on the large model


def test_tool_results_reach_the_model_prepared_and_the_last_turn_closes_the_exploration() -> None:
    final = recorded().interactions[3].request
    lookup, read_and_callers, closing = (final["messages"][index] for index in (2, 4, 6))
    (found,) = blocks(lookup, "tool_result")
    assert found["content"].startswith("Matches for 'Cart.total', best first:")
    assert '<evidence id="E1" path="src/shop/cart.py" lines="17-19"/>' in found["content"]
    read, callers = blocks(read_and_callers, "tool_result")
    assert "18 |     def total(self) -> Decimal:" in read["content"]
    assert (
        read["content"]
        .rstrip()
        .endswith('<evidence id="E1" path="src/shop/cart.py" lines="17-19"/>')
    )
    assert "is_error" not in read and "is_error" not in callers
    # The last user turn: the final_answer result, then the instruction to write the answer.
    assert [block["type"] for block in closing["content"]] == ["tool_result", "text"]
    assert closing["content"][0]["content"] == "The exploration has ended."
    assert closing["content"][1]["text"] == closing_text("final_answer")


def test_the_cassette_is_marked_hand_written_and_holds_no_secrets() -> None:
    raw = AGENT_CASSETTE.read_text(encoding="utf-8")
    cassette = Cassette.model_validate_json(raw)
    assert "sk-ant-" not in raw and "\r" not in raw
    if cassette.source == "docs":
        assert cassette.note == DOCS_NOTE  # says plainly that no model returned these
    else:  # after the live pass: recorded from the API, with no leftover note
        assert cassette.source == "live" and cassette.note is None
    assert json.loads(raw)["description"].startswith("agent answer on py_small")
