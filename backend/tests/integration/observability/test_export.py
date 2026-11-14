"""The API with OpenTelemetry export off and on (GRD-2), against a collector in this process.

The collector is an HTTP server that accepts OTLP/HTTP (``POST /v1/traces``, protobuf) and
decodes what arrives. The question is answered by ``FakeLLM`` on a stored fixture snapshot.
"""

from __future__ import annotations

import gzip
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from repoatlas.api.app import API_PREFIX, create_app
from repoatlas.config import Settings
from repoatlas.llm.fake import FakeLLM
from repoatlas.observability import otel
from tests.integration.api.test_ask import QUESTION, ask, drafting, events, install
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.api.helpers import api_settings, client_for

pytestmark = pytest.mark.anyio
SECRET = "Basic dGVzdDpzZWtyaXQ="


class Collector:
    """What an OTLP/HTTP destination sees: requests, their headers, and the decoded spans."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.spans: list[dict[str, Any]] = []
        self.services: set[str] = set()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                names = {name.lower(): value for name, value in self.headers.items()}
                collector.receive(self.path, names, body)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def receive(self, path: str, headers: dict[str, str], body: bytes) -> None:
        self.paths.append(path)
        self.headers.append(headers)
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        for resource in request.resource_spans:
            for item in resource.resource.attributes:
                if item.key == "service.name":
                    self.services.add(item.value.string_value)
            for scope in resource.scope_spans:
                for span in scope.spans:
                    self.spans.append(
                        {
                            "name": span.name,
                            "trace_id": span.trace_id.hex(),
                            "span_id": span.span_id.hex(),
                            "parent": span.parent_span_id.hex(),
                            "scope": scope.scope.name,
                            "attributes": {
                                a.key: a.value.WhichOneof("value")
                                and getattr(a.value, a.value.WhichOneof("value") or "")
                                for a in span.attributes
                            },
                        }
                    )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def collector() -> Iterator[Collector]:
    collector = Collector()
    yield collector
    collector.close()


@pytest.fixture
def exporting(tmp_path: Path, pg_url: str, collector: Collector, clean_store: None) -> Any:
    """An app whose settings name the collector, with tracing configured from them."""

    def build(endpoint: str | None) -> tuple[FastAPI, Settings]:
        settings = api_settings(
            database_url=pg_url,
            data_dir=tmp_path / "data",
            otel_exporter_otlp_endpoint=endpoint,
            otel_exporter_otlp_headers=f"Authorization={SECRET.replace(' ', '%20')}",
            otel_service_name="repoatlas-test",
            otel_export_timeout_s=2.0,
        )
        otel.configure(settings, force=True)
        return create_app(settings), settings

    yield build
    otel.shutdown()


async def answered(app: FastAPI) -> dict[str, Any]:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, FakeLLM(lambda call: drafting(call)))
    response = await ask(app, snapshot_id)
    assert response.status_code == 200
    stream = events(response.text)
    assert [name for name, _ in stream][-2:] == ["answer", "done"]
    done: dict[str, Any] = stream[-1][1]
    async with client_for(app) as client:
        trace = (await client.get(f"{API_PREFIX}/traces/{done['trace_id']}")).json()
    assert {span["name"] for span in trace["spans"]} >= {"answer", "retrieve", "validate"}
    return done


async def test_export_on_sends_one_connected_trace(exporting: Any, collector: Collector) -> None:
    app, _ = exporting(collector.url)
    try:
        done = await answered(app)
        assert otel.flush()
    finally:
        app.state.engine.dispose()
    assert set(collector.paths) == {"/v1/traces"}
    assert collector.services == {"repoatlas-test"}
    assert [headers.get("authorization") for headers in collector.headers] == [SECRET] * len(
        collector.headers
    ), collector.headers

    spans = collector.spans
    server = next(s for s in spans if s["name"].startswith("POST ") and s["name"].endswith("/ask"))
    in_request = [s for s in spans if s["trace_id"] == server["trace_id"]]
    names = {s["name"] for s in in_request}
    assert {"answer", "retrieve", "validate"} <= names
    # RepoAtlas's steps hang under the request's span, and name the stored trace.
    answer = next(s for s in in_request if s["name"] == "answer")
    by_id = {s["span_id"]: s for s in in_request}
    ancestor, hops = answer, 0
    while ancestor["parent"] and ancestor["span_id"] != server["span_id"] and hops < 10:
        ancestor, hops = by_id[ancestor["parent"]], hops + 1
    assert ancestor["span_id"] == server["span_id"]
    assert answer["attributes"]["repoatlas.trace_id"] == done["trace_id"]
    assert uuid.UUID(answer["attributes"]["repoatlas.span_id"])
    retrieve = next(s for s in in_request if s["name"] == "retrieve")
    assert retrieve["parent"] == answer["span_id"]
    # SQL statements are spans of the same trace (from the SQLAlchemy instrumentation).
    sql = [s for s in in_request if "sqlalchemy" in s["scope"]]
    assert sql and all("db.statement" in s["attributes"] for s in sql)
    # Nothing a span carries is a secret, a prompt, or repo text.
    dumped = repr(spans)
    assert "sekrit" not in dumped and SECRET not in dumped
    assert QUESTION not in dumped and "def total" not in dumped


async def test_export_off_sends_nothing_and_answers(exporting: Any, collector: Collector) -> None:
    app, settings = exporting(None)
    try:
        assert otel.exporter_for(settings) is None
        await answered(app)
        assert otel.flush()
    finally:
        app.state.engine.dispose()
    assert collector.paths == []


async def test_an_unreachable_collector_never_breaks_an_answer(
    exporting: Any, collector: Collector
) -> None:
    url = collector.url
    collector.close()  # nothing listens there any more
    app, _ = exporting(url)
    try:
        await answered(app)
        otel.flush(timeout_s=3.0)  # the export fails; the answer above already succeeded
    finally:
        app.state.engine.dispose()
