"""Traces and spans in Postgres: writing, reading back, cascades, and the schema."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

from repoatlas.llm.types import LLMResult, Usage
from repoatlas.tracing import start_trace, trace_span
from repoatlas.tracing.models import TRACING_TABLES
from repoatlas.tracing.store import SqlTraceSink, load_trace


@pytest.fixture
def clean_traces(pg_engine: Engine) -> Iterator[None]:
    yield
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE traces, spans"))


def test_tables_exist(pg_engine: Engine) -> None:
    assert set(TRACING_TABLES) <= set(inspect(pg_engine).get_table_names())


def test_a_trace_is_written_and_read_back(pg_engine: Engine, clean_traces: None) -> None:
    with start_trace("answer", sink=SqlTraceSink(pg_engine)) as trace:
        with trace_span("retrieve", "retrieval", mode="hybrid"):
            pass
        with trace_span("llm.call", "llm") as span:
            span.record_llm(
                LLMResult(
                    content="",
                    model="claude-sonnet-5-5",
                    usage=Usage(input_tokens=100, output_tokens=20, cache_read_tokens=50),
                    cost_usd=0.25,
                    stop_reason="end_turn",
                    latency_ms=12.5,
                )
            )
    view = load_trace(pg_engine, trace.id)
    assert view is not None
    assert (view.kind, view.status, view.snapshot_id) == ("answer", "ok", None)
    assert (view.tokens_in, view.tokens_out, view.cost_usd) == (150, 20, 0.25)
    assert [span.name for span in view.spans] == ["retrieve", "llm.call"]
    llm_span = view.spans[1]
    assert llm_span.model == "claude-sonnet-5-5"
    assert llm_span.cache_read_tokens == 50
    assert llm_span.attrs["latency_ms"] == 12.5
    assert view.spans[0].attrs["mode"] == "hybrid"


def test_unknown_traces_are_none(pg_engine: Engine) -> None:
    assert load_trace(pg_engine, uuid.uuid4()) is None


def test_deleting_a_trace_deletes_its_spans(pg_engine: Engine, clean_traces: None) -> None:
    with start_trace("answer", sink=SqlTraceSink(pg_engine)) as trace, trace_span("s", "step"):
        pass
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM traces WHERE id = :id"), {"id": trace.id})
        left = connection.execute(
            text("SELECT count(*) FROM spans WHERE trace_id = :id"), {"id": trace.id}
        ).scalar_one()
    assert left == 0
