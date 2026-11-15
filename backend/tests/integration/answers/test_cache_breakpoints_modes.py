"""Every answering mode's requests carry the cache breakpoints (PRF-5, decision 0024).

Each mode answers one question on a built snapshot through the real ``LLMClient``, with the
offline stand-in as its transport (no model, decision 0020), and every request it sent is
checked: the static prefix is marked on its last tool definition and its last system block; a
call that may use tools also marks the last block of its conversation; no other call does.
Whether the API then reports cache reads is live-pass item 21.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from repoatlas.agent.factory import agent_system
from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.system import OneShotAnswerSystem
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.config import Settings
from repoatlas.llm.client import LLMClient
from repoatlas.llm.compat import MAX_CACHE_BREAKPOINTS, count_cache_breakpoints
from repoatlas.llm.offline import OfflineTransport
from repoatlas.llm.pricing import PriceTable
from repoatlas.multiagent.factory import multiagent_budget, multiagent_system
from repoatlas.pipeline.build import build_snapshot
from repoatlas.retrieval.embed import embedder_for
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from tests.integration.origin import URL, Origin, serve_origin

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("clean_store")]

QUESTION = "What does main call, and what does helper return?"
EPHEMERAL = {"type": "ephemeral"}


class Recording(OfflineTransport):
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        return await super().send(request, timeout_s=timeout_s)


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Origin:
    return serve_origin(tmp_path, monkeypatch)


@pytest.fixture
def settings(tmp_path: Path, pg_url: str) -> Settings:
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", database_url=pg_url, llm_transport="offline"
    )


@pytest.fixture
def snapshot_id(origin: Origin, settings: Settings, pg_engine: Engine) -> int:
    return build_snapshot(URL, settings, engine=pg_engine)


def client(settings: Settings, transport: Recording) -> LLMClient:
    return LLMClient(
        transport,
        models={"small": settings.llm_small_model, "large": settings.llm_large_model},
        efforts={"small": settings.llm_small_effort, "large": settings.llm_large_effort},
        prices=PriceTable(settings.llm_prices),
    )


def message_marks(request: dict[str, Any]) -> list[tuple[int, int]]:
    return [
        (m, b)
        for m, message in enumerate(request["messages"])
        if isinstance(message["content"], list)
        for b, block in enumerate(message["content"])
        if "cache_control" in block
    ]


def check(request: dict[str, Any]) -> str:
    """Check one request's breakpoints; return which kind of call it was."""
    assert request["system"], "every call has a static system prompt"
    marked_system = [i for i, block in enumerate(request["system"]) if "cache_control" in block]
    assert marked_system == [len(request["system"]) - 1]
    assert request["system"][-1]["cache_control"] == EPHEMERAL
    tools = request.get("tools", [])
    assert [i for i, tool in enumerate(tools) if "cache_control" in tool] == (
        [len(tools) - 1] if tools else []
    )
    exploring = bool(tools) and request.get("tool_choice") == {"type": "auto"}
    marks = message_marks(request)
    if exploring:
        last = request["messages"][-1]
        assert last["role"] == "user"
        assert marks == [(len(request["messages"]) - 1, len(last["content"]) - 1)]
    else:
        assert marks == []
    assert count_cache_breakpoints(request) == len(marked_system) + bool(tools) + len(marks)
    assert count_cache_breakpoints(request) <= MAX_CACHE_BREAKPOINTS - 1
    return "explore" if exploring else ("closing" if tools else "plain")


def kinds(transport: Recording) -> list[tuple[str, str]]:
    return [(request["model"], check(request)) for request in transport.requests]


def answered(result: AnswerResult) -> None:
    assert not result.refused, result.refusal_reason
    assert result.citations


async def test_a_one_shot_answer_marks_only_its_system_prompt(
    snapshot_id: int, settings: Settings, pg_engine: Engine
) -> None:
    transport = Recording()
    system = OneShotAnswerSystem(
        Retriever(GraphStore.from_settings(settings, pg_engine), embedder_for(settings)),
        client(settings, transport),
        CitationValidator(StoreBlobs(pg_engine)),
        RefusalPolicy.from_settings(settings),
        prices=PriceTable(settings.llm_prices),
        evidence_tokens=settings.answer_evidence_tokens,
    )
    answered(await system.answer(snapshot_id, QUESTION, Budget()))
    # One call: the evidence in its user message is never sent again, so nothing marks it.
    assert kinds(transport) == [(settings.llm_large_model, "plain")]


async def test_an_agent_marks_its_conversation_while_it_explores(
    snapshot_id: int, settings: Settings, pg_engine: Engine
) -> None:
    transport = Recording()
    system = agent_system(settings, pg_engine, client(settings, transport))
    result = await system.answer(snapshot_id, QUESTION, Budget())
    answered(result)
    found = kinds(transport)
    small, large = settings.llm_small_model, settings.llm_large_model
    # The stand-in searches once and finishes: two exploring calls, then the final call on
    # the large model, which may not use tools and so marks only the static prefix.
    assert found == [(small, "explore"), (small, "explore"), (large, "closing")]
    first, second = transport.requests[:2]
    assert len(second["messages"]) == len(first["messages"]) + 2
    assert message_marks(first) == [(0, 0)] and message_marks(second)[0][0] == 2


async def test_the_pipeline_marks_each_stage_as_its_kind_of_call(
    snapshot_id: int, settings: Settings, pg_engine: Engine
) -> None:
    transport = Recording()
    system = multiagent_system(settings, pg_engine, client(settings, transport))
    result = await system.answer(snapshot_id, QUESTION, multiagent_budget(settings))
    answered(result)
    found = kinds(transport)
    small, large = settings.llm_small_model, settings.llm_large_model
    # Planner, then one branch (explore twice, notes with the tools but no tool use), then
    # the writer and the verifier.
    assert found == [
        (large, "plain"),
        (small, "explore"),
        (small, "explore"),
        (small, "closing"),
        (large, "plain"),
        (large, "plain"),
    ]
    # The notes call continues the branch's conversation on the same model: its static
    # prefix (tools and system prompt) is byte for byte the exploring calls'.
    explore, notes = transport.requests[2], transport.requests[3]
    assert notes["tools"] == explore["tools"] and notes["system"] == explore["system"]


async def test_the_conversation_breakpoint_follows_the_setting(
    snapshot_id: int, settings: Settings, pg_engine: Engine
) -> None:
    transport = Recording()
    llm = client(settings, transport)
    llm.cache_conversation = False
    answered(await agent_system(settings, pg_engine, llm).answer(snapshot_id, QUESTION, Budget()))
    assert all(message_marks(request) == [] for request in transport.requests)
    assert all(count_cache_breakpoints(request) == 2 for request in transport.requests)
