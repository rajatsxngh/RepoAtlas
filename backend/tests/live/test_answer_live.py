"""Records the end-to-end answer cassette (``tests/llm_cassettes/answers``) from the real API.

Opt in: ``uv run poe record-cassettes`` (needs Postgres and REPOATLAS_ANTHROPIC_API_KEY). One
large-model call, about $0.02.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from tests.cassettes import RECORD_ENV, finish, use_cassette
from tests.integration.answers.recorded import ANSWER_CASSETTE, answer_with
from tests.integration.retrieval.conftest import store_fixture

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_record_the_answer(
    pg_engine: Engine, clean_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(RECORD_ENV, "1")
    snapshot_id = store_fixture(pg_engine, "py_small")
    transport = use_cassette(ANSWER_CASSETTE, description="one-shot answer on py_small")
    result = await answer_with(transport, pg_engine, snapshot_id)
    assert not result.refused, result.refusal_reason
    finish(transport)
