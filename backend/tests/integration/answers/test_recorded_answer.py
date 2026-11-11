"""A recorded answer, replayed through the whole one-shot system: the prompt the system sends
must still match the recording, and the recorded draft must validate against the stored blobs."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from repoatlas.llm.cassette import Cassette
from tests.cassettes import finish, use_cassette
from tests.integration.answers.recorded import ANSWER_CASSETTE, answer_with
from tests.integration.retrieval.conftest import store_fixture

pytestmark = pytest.mark.anyio


async def test_the_recorded_answer_replays_and_validates(
    pg_engine: Engine, clean_store: None
) -> None:
    snapshot_id = store_fixture(pg_engine, "py_small")
    transport = use_cassette(ANSWER_CASSETTE)
    result = await answer_with(transport, pg_engine, snapshot_id)
    finish(transport)
    assert not result.refused, result.refusal_reason
    assert result.citations
    assert all(c.ref.path == "src/shop/cart.py" for c in result.citations)
    assert result.metrics.prefilter_citation_validity is not None
    assert result.usage.output_tokens > 0
    assert result.trace_id is not None


def test_the_cassette_holds_no_secrets() -> None:
    raw = ANSWER_CASSETTE.read_text(encoding="utf-8")
    assert "sk-ant-" not in raw
    Cassette.model_validate_json(raw)
