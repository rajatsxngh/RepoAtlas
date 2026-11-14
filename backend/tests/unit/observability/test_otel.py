"""OpenTelemetry mirroring (GRD-2): spans nest like ``trace_span`` does, carry model, tokens,
and cost, and nothing is recorded or sent unless tracing is configured."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import anyio
import httpx
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from repoatlas.llm.types import LLMResult, Usage
from repoatlas.observability import otel
from repoatlas.tracing import start_trace, trace_span
from tests.unit.api.helpers import api_settings, client_for


@pytest.fixture
def exported() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    otel.configure(api_settings(), span_processors=[SimpleSpanProcessor(exporter)], force=True)
    yield exporter
    otel.shutdown()


def by_name(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {span.name: span for span in exporter.get_finished_spans()}


def parent_id(span: ReadableSpan) -> int | None:
    return None if span.parent is None else span.parent.span_id


def test_spans_nest_like_trace_span(exported: InMemorySpanExporter) -> None:
    with start_trace("answer", 7) as trace, trace_span("answer", "answer", system="one") as root:
        with trace_span("retrieve", "retrieval", evidence=3):
            pass
        with trace_span("validate", "validation"):
            pass
    spans = by_name(exported)
    assert set(spans) == {"answer", "retrieve", "validate"}
    answer = spans["answer"]
    assert parent_id(answer) is None
    assert parent_id(spans["retrieve"]) == answer.context.span_id
    assert parent_id(spans["validate"]) == answer.context.span_id
    assert len({span.context.trace_id for span in spans.values()}) == 1
    attributes = dict(spans["retrieve"].attributes or {})
    assert attributes["repoatlas.kind"] == "retrieval"
    assert attributes["repoatlas.evidence"] == 3
    assert attributes["repoatlas.trace_id"] == str(trace.id)
    assert attributes["repoatlas.trace_kind"] == "answer"
    assert attributes["repoatlas.snapshot_id"] == 7
    assert (answer.attributes or {})["repoatlas.span_id"] == str(root.id)
    assert (answer.attributes or {})["repoatlas.system"] == "one"


@pytest.mark.anyio
async def test_nesting_survives_threads_and_tasks(exported: InMemorySpanExporter) -> None:
    def in_thread() -> None:
        with trace_span("thread-step", "step"):
            pass

    async def in_task(name: str) -> None:
        with trace_span(name, "step"):
            await anyio.sleep(0)

    with start_trace("answer"), trace_span("root", "answer"):
        await anyio.to_thread.run_sync(in_thread)
        async with anyio.create_task_group() as group:
            group.start_soon(in_task, "task-a")
            group.start_soon(in_task, "task-b")
    spans = by_name(exported)
    root = spans["root"].context.span_id
    assert {parent_id(spans[name]) for name in ("thread-step", "task-a", "task-b")} == {root}


def test_llm_calls_and_errors_are_mirrored(exported: InMemorySpanExporter) -> None:
    result = LLMResult(
        content="{}",
        model="claude-sonnet-5-5",
        usage=Usage(input_tokens=1200, output_tokens=300, cache_read_tokens=800),
        cost_usd=0.0054,
        stop_reason="end_turn",
        latency_ms=12.0,
    )
    with pytest.raises(ValueError), start_trace("answer"):
        with trace_span("llm.large", "llm") as span:
            span.record_llm(result)
        with trace_span("bad", "step", items=["not", "a", "scalar"]):
            raise ValueError("boom")
    spans = by_name(exported)
    llm = dict(spans["llm.large"].attributes or {})
    assert llm["gen_ai.request.model"] == "claude-sonnet-5-5"
    assert llm["gen_ai.usage.input_tokens"] == 2000  # uncached and cached input
    assert llm["gen_ai.usage.output_tokens"] == 300
    assert llm["gen_ai.usage.cache_read_input_tokens"] == 800
    assert llm["gen_ai.usage.cost"] == pytest.approx(0.0054)
    assert llm["repoatlas.stop_reason"] == "end_turn"
    assert spans["llm.large"].status.status_code is not StatusCode.ERROR
    bad = spans["bad"]
    assert bad.status.status_code is StatusCode.ERROR
    assert "ValueError: boom" in (bad.status.description or "")
    assert "repoatlas.items" not in (bad.attributes or {})  # lists are left out


def test_nothing_is_recorded_until_configured() -> None:
    otel.shutdown()
    assert not otel.configured()
    with start_trace("answer") as trace, trace_span("quiet", "step"):
        pass
    assert [span.name for span in trace.spans] == ["quiet"]  # the trace store is unaffected
    assert not otel.tracer().start_span("x").is_recording()


def test_configure_is_done_once_per_process(exported: InMemorySpanExporter) -> None:
    first = otel.configure(api_settings())
    assert otel.configure(api_settings(otel_service_name="other")) is first
    assert otel.configured()


@pytest.mark.anyio
async def test_a_request_span_is_the_parent_of_its_steps(exported: InMemorySpanExporter) -> None:
    app = FastAPI()

    @app.get("/work")
    async def work() -> dict[str, bool]:
        with start_trace("answer"), trace_span("step", "step"):
            pass
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    otel.instrument_app(app)
    async with client_for(app) as client:
        assert (await client.get("/work")).status_code == 200
        assert (await client.get("/health")).status_code == 200
    finished = exported.get_finished_spans()
    server = [span for span in finished if span.kind is SpanKind.SERVER]
    assert [span.name for span in server] == ["GET /work"]  # probes are not traced
    step = next(span for span in finished if span.name == "step")
    assert parent_id(step) == server[0].context.span_id


def test_outgoing_http_calls_are_spans_without_headers(exported: InMemorySpanExporter) -> None:
    # The instrumentation wraps httpx's real transports, so this needs a real (local) server.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with start_trace("build"), trace_span("fetch", "step"), httpx.Client() as client:
            url = f"http://127.0.0.1:{server.server_port}/repos/a/b"
            client.get(url, headers={"Authorization": "Bearer sekrit"})
    finally:
        server.shutdown()
        server.server_close()
    finished = exported.get_finished_spans()
    call = next(span for span in finished if span.kind is SpanKind.CLIENT)
    fetch = next(span for span in finished if span.name == "fetch")
    assert parent_id(call) == fetch.context.span_id
    assert "sekrit" not in repr(dict(call.attributes or {}))


def test_endpoint_and_header_parsing() -> None:
    assert otel.traces_endpoint("http://localhost:4318/") == "http://localhost:4318/v1/traces"
    assert otel.traces_endpoint("https://x.test/api/public/otel/v1/traces") == (
        "https://x.test/api/public/otel/v1/traces"
    )
    assert otel.parse_headers("Authorization=Basic%20abc, x-team = map ,broken") == {
        "Authorization": "Basic abc",
        "x-team": "map",
    }
    assert otel.parse_headers(None) == {}
    assert otel.exporter_for(api_settings()) is None  # off unless the endpoint is set


def test_settings_read_the_standard_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from repoatlas.config import Settings

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic abc")
    settings = Settings(_env_file=None)
    assert settings.otel_exporter_otlp_endpoint == "http://collector:4318"
    assert "abc" not in repr(settings)
    assert settings.otel_service_name == "repoatlas"
