"""Records the agent conversation cassette (``tests/llm_cassettes/agent``) from the real API.

Opt in: ``uv run poe record-cassettes`` (needs Postgres and REPOATLAS_ANTHROPIC_API_KEY). A few
small-model calls and one large-model call, a few cents. It replaces the hand-written cassette
(decision 0022; ``docs/live-pass.md``, item 10).

A real model explores as it sees fit, so the recorded conversation will differ from the
hand-written one in its steps. After recording, ``tests/integration/agent/test_recorded_agent.py``
needs its expectations about the particular steps (which tools, how many turns, the usage sums)
updated to the recording; its checks of the request rules stay as they are.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from tests.cassettes import RECORD_ENV, finish, use_cassette
from tests.integration.agent.recorded import AGENT_CASSETTE, agent_answer_with
from tests.integration.retrieval.conftest import store_fixture

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_record_the_agent_conversation(
    pg_engine: Engine, clean_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RECORD_ENV, "1")
    snapshot_id = store_fixture(pg_engine, "py_small")
    transport = use_cassette(
        AGENT_CASSETTE,
        description="agent answer on py_small: exploration turns and the final answer",
    )
    result = await agent_answer_with(transport, pg_engine, snapshot_id)
    # Whatever the model chose to do, the run must have gone through tools to a final call.
    assert result.tool_uses, "the model made no tool call"
    assert result.refusal_kind != "api", result.refusal_reason
    finish(transport)
