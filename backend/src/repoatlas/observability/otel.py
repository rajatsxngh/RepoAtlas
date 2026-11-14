"""OpenTelemetry for RepoAtlas (GRD-2, decision 0014).

``configure(settings)`` makes the process's tracer provider and instruments httpx; the API also
instruments its FastAPI app and SQLAlchemy engine (``instrument_app``, ``instrument_engine``).
Every ``trace_span`` is mirrored as an OpenTelemetry span through ``tracer()``, so a request's
HTTP span, its database queries, its outgoing HTTP calls, and RepoAtlas's own steps (with model,
tokens, and cost) form one trace.

Nothing leaves the process unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. With it, spans are
sent in batches over OTLP/HTTP to ``<endpoint>/v1/traces`` (Langfuse, a collector, or any OTLP
destination), with the headers from ``OTEL_EXPORTER_OTLP_HEADERS`` (``key=value`` pairs
separated by commas, URL-encoded values; typically the destination's ``Authorization``). The
headers are a secret: they are never logged or put on a span. RepoAtlas's own trace store works
the same with export on or off.

Span attributes hold names, counts, and numbers only: no prompts, no answers, no repo text, no
request headers.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.trace import Tracer

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy import Engine

    from repoatlas.config import Settings

TRACER_NAME = "repoatlas"
TRACES_PATH = "/v1/traces"
# Liveness and readiness probes would otherwise be most of the exported spans.
EXCLUDED_URLS = "health,ready"

_lock = threading.Lock()
_provider: TracerProvider | None = None
_noop: Tracer = otel_trace.NoOpTracer()


def tracer() -> Tracer:
    """The tracer ``trace_span`` mirrors into: a no-op until ``configure`` has run."""
    provider = _provider
    return _noop if provider is None else provider.get_tracer(TRACER_NAME)


def configured() -> bool:
    return _provider is not None


def parse_headers(value: str | None) -> dict[str, str]:
    """``key=value,key2=value2`` (the standard ``OTEL_EXPORTER_OTLP_HEADERS`` form)."""
    headers: dict[str, str] = {}
    for pair in (value or "").split(","):
        name, sep, text = pair.partition("=")
        if sep and name.strip():
            headers[name.strip()] = unquote(text.strip())
    return headers


def traces_endpoint(base: str) -> str:
    """The traces URL for a base endpoint (a URL that already ends in it is kept)."""
    trimmed = base.strip().rstrip("/")
    return trimmed if trimmed.endswith(TRACES_PATH) else trimmed + TRACES_PATH


def exporter_for(settings: Settings) -> SpanProcessor | None:
    """The batching OTLP/HTTP exporter, or ``None`` when no endpoint is set."""
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    secret = settings.otel_exporter_otlp_headers
    headers = parse_headers(secret.get_secret_value() if secret is not None else None)
    exporter = OTLPSpanExporter(
        endpoint=traces_endpoint(endpoint),
        headers=headers or None,
        timeout=settings.otel_export_timeout_s,
    )
    return BatchSpanProcessor(exporter)


def configure(
    settings: Settings,
    *,
    span_processors: Sequence[SpanProcessor] | None = None,
    force: bool = False,
) -> TracerProvider:
    """Set up tracing for this process, once. A second call returns the same provider (``force``
    replaces it; tests use that with an in-memory exporter in ``span_processors``)."""
    global _provider
    with _lock:
        if _provider is not None and not force:
            return _provider
        if _provider is not None:
            _shutdown_locked()
        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        processors = list(span_processors or [])
        exporter = exporter_for(settings)
        if exporter is not None:
            processors.append(exporter)
        for processor in processors:
            provider.add_span_processor(processor)
        _instrument_httpx(provider)
        _provider = provider
        return provider


def instrument_app(app: FastAPI) -> None:
    """One server span per API request (probes excluded), the parent of everything it does."""
    provider = _provider
    if provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=EXCLUDED_URLS,
        exclude_spans=["receive", "send"],
    )


def instrument_engine(engine: Engine) -> None:
    """One client span per SQL statement (the statement text, never its parameters)."""
    provider = _provider
    if provider is None:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)


def _instrument_httpx(provider: TracerProvider) -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    instrumentor = HTTPXClientInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    instrumentor.instrument(tracer_provider=provider)


def flush(timeout_s: float = 5.0) -> bool:
    """Send what is buffered (before a short-lived process exits, and in tests)."""
    provider = _provider
    return True if provider is None else provider.force_flush(int(timeout_s * 1000))


def _shutdown_locked() -> None:
    global _provider
    provider, _provider = _provider, None
    if provider is None:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    instrumentor = HTTPXClientInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    provider.shutdown()


def shutdown() -> None:
    """Flush and stop exporting; ``tracer()`` is a no-op again."""
    with _lock:
        _shutdown_locked()


def span_attributes(attrs: Mapping[str, Any], prefix: str = "repoatlas.") -> dict[str, Any]:
    """``attrs`` as OpenTelemetry attributes: scalars only, names prefixed."""
    return {
        f"{prefix}{name}": value
        for name, value in attrs.items()
        if isinstance(value, str | bool | int | float)
    }
