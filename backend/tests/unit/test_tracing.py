"""Tracing core: nesting, async tasks, errors, LLM span attributes, trace totals."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from repoatlas.llm.client import LLMClient
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.types import LLMRequestError, LLMResult, Usage
from repoatlas.tracing import current_trace, current_trace_id, start_trace, trace_span
from repoatlas.tracing.core import Trace


class Collect:
    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def write(self, trace: Trace) -> None:
        self.traces.append(trace)


def by_name(trace: Trace) -> dict[str, Any]:
    return {span.name: span for span in trace.spans}


def test_nested_spans_and_the_current_trace() -> None:
    sink = Collect()
    assert current_trace_id() is None
    with start_trace("answer", 7, sink=sink) as trace:
        assert current_trace() is trace
        assert current_trace_id() == trace.id
        with trace_span("outer", "step", a=1) as outer, trace_span("inner", "step") as inner:
            inner.set(b=2)
        with trace_span("sibling", "step"):
            pass
    assert current_trace_id() is None
    assert sink.traces == [trace]
    assert trace.status == "ok"
    assert trace.snapshot_id == 7
    spans = by_name(trace)
    assert spans["outer"].parent_id is None
    assert spans["inner"].parent_id == outer.id
    assert spans["sibling"].parent_id is None
    assert spans["outer"].attrs["a"] == 1
    assert spans["inner"].attrs["b"] == 2
    assert all(span.status == "ok" and span.ended_at for span in trace.spans)
    assert all("duration_ms" in span.attrs for span in trace.spans)


def test_errors_mark_the_span_and_the_trace() -> None:
    sink = Collect()
    with pytest.raises(ValueError), start_trace("answer", sink=sink) as trace:
        with trace_span("ok", "step"):
            pass
        with trace_span("bad", "step"):
            raise ValueError("boom")
    spans = by_name(trace)
    assert spans["bad"].status == "error"
    assert spans["bad"].attrs["error"] == "ValueError: boom"
    assert spans["ok"].status == "ok"
    assert trace.status == "error"
    assert sink.traces == [trace]


def test_a_trace_ends_once() -> None:
    sink = Collect()
    trace = start_trace("answer", sink=sink)
    trace.end()
    trace.end("error")
    assert len(sink.traces) == 1
    assert trace.status == "ok"


def test_spans_outside_a_trace_are_not_recorded() -> None:
    with trace_span("loose", "step") as span:
        span.set(x=1)
    assert span.record.status == "ok"


@pytest.mark.anyio
async def test_spans_nest_across_await_and_tasks() -> None:
    async def step(name: str, delay: float) -> None:
        with trace_span(name, "step"):
            await asyncio.sleep(delay)
            with trace_span(f"{name}.child", "step"):
                await asyncio.sleep(0)

    with start_trace("answer") as trace, trace_span("parent", "step") as parent:
        await asyncio.gather(step("a", 0.01), step("b", 0))
    spans = by_name(trace)
    assert spans["a"].parent_id == parent.id
    assert spans["b"].parent_id == parent.id
    assert spans["a.child"].parent_id == spans["a"].id
    assert spans["b.child"].parent_id == spans["b"].id


class OneShot:
    def __init__(self, *items: Any) -> None:
        self.items = list(items)

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)


def llm(*items: Any) -> LLMClient:
    return LLMClient(
        OneShot(*items),
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        prices=PriceTable(DEFAULT_PRICES),
    )


RESPONSE = {
    "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": "hi"}],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 4000,
        "cache_creation_input_tokens": 0,
    },
}


@pytest.mark.anyio
async def test_llm_calls_record_their_span_and_traces_sum_them() -> None:
    client = llm(RESPONSE, RESPONSE)
    with start_trace("answer") as trace:
        with trace_span("answer", "step"):
            first = await client.complete("large", [{"role": "user", "content": "q"}], max_tokens=5)
        await client.complete("large", [{"role": "user", "content": "q"}], max_tokens=5)
    llm_spans = [span for span in trace.spans if span.kind == "llm"]
    assert len(llm_spans) == 2
    span = llm_spans[0]
    assert span.parent_id == by_name(trace)["answer"].id
    assert span.model == "claude-sonnet-5-5"
    assert (span.tokens_in, span.tokens_out, span.cache_read_tokens) == (5000, 200, 4000)
    assert span.cost_usd == pytest.approx(first.cost_usd)
    assert span.attrs["stop_reason"] == "end_turn"
    assert span.attrs["role"] == "large"
    assert span.attrs["latency_ms"] == first.latency_ms
    assert trace.tokens_in == 10_000
    assert trace.tokens_out == 400
    assert trace.cost_usd == pytest.approx(2 * first.cost_usd)


@pytest.mark.anyio
async def test_failed_llm_calls_leave_an_error_span() -> None:
    client = llm(LLMRequestError("bad", status=400))
    with start_trace("answer") as trace, pytest.raises(LLMRequestError):
        await client.complete("small", [{"role": "user", "content": "q"}], max_tokens=5)
    (span,) = trace.spans
    assert span.status == "error"
    assert span.tokens_in is None


def test_record_llm_accumulates() -> None:
    result = LLMResult(content="", model="m", usage=Usage(input_tokens=3), cost_usd=0.5)
    with trace_span("x", "llm") as span:
        span.record_llm(result)
        span.record_llm(result)
    assert span.record.tokens_in == 6
    assert span.record.cost_usd == 1.0
