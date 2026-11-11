"""Traces and spans in process (ANS-5, decision 0014).

``start_trace(kind, snapshot_id)`` makes a trace; used as a context manager it becomes the
current trace, and leaving it ends the trace (status ``error`` if an exception escaped).
``trace_span(name, kind, **attrs)`` times one step of the current trace; spans nest through
context variables, so they are safe across ``await`` and in tasks started inside a span. A span
outside any trace is timed but not recorded.

LLM calls record their model, tokens, cache tokens, cost, latency, and stop reason on their
span (``Span.record_llm``, called by ``LLMClient``). When a trace ends, its totals are summed
from its spans and the trace is handed to its sink (``SqlTraceSink`` writes it to Postgres).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol

from repoatlas.llm.types import LLMResult
from repoatlas.tracing.models import TraceStatus

MAX_ERROR_CHARS = 500


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SpanRecord:
    id: uuid.UUID
    trace_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    started_at: datetime
    ended_at: datetime | None = None
    status: TraceStatus = "running"
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


class Span:
    """The handle a ``trace_span`` block gets: add attributes, record an LLM call."""

    def __init__(self, record: SpanRecord) -> None:
        self.record = record

    @property
    def id(self) -> uuid.UUID:
        return self.record.id

    def set(self, **attrs: Any) -> None:
        self.record.attrs.update(attrs)

    def record_llm(self, result: LLMResult) -> None:
        record = self.record
        record.model = result.model
        record.tokens_in = (record.tokens_in or 0) + result.usage.total_input_tokens
        record.tokens_out = (record.tokens_out or 0) + result.usage.output_tokens
        record.cache_read_tokens = (record.cache_read_tokens or 0) + result.usage.cache_read_tokens
        record.cost_usd = (record.cost_usd or 0.0) + result.cost_usd
        record.attrs.update(
            cache_write_tokens=result.usage.cache_write_tokens,
            input_tokens_uncached=result.usage.input_tokens,
            latency_ms=result.latency_ms,
            stop_reason=result.stop_reason,
            attempts=result.attempts,
            refused=result.refused,
        )


class TraceSink(Protocol):
    def write(self, trace: Trace) -> None: ...


class Trace:
    def __init__(self, kind: str, snapshot_id: int | None, sink: TraceSink | None) -> None:
        self.id = uuid.uuid4()
        self.kind = kind
        self.snapshot_id = snapshot_id
        self.sink = sink
        self.started_at = _now()
        self.ended_at: datetime | None = None
        self.status: TraceStatus = "running"
        self.spans: list[SpanRecord] = []
        self._lock = threading.Lock()
        self._token: Token[Trace | None] | None = None

    def add(self, record: SpanRecord) -> None:
        with self._lock:
            self.spans.append(record)

    @property
    def tokens_in(self) -> int:
        return sum(span.tokens_in or 0 for span in self.spans)

    @property
    def tokens_out(self) -> int:
        return sum(span.tokens_out or 0 for span in self.spans)

    @property
    def cost_usd(self) -> float:
        return sum(span.cost_usd or 0.0 for span in self.spans)

    def end(self, status: TraceStatus = "ok") -> None:
        """End the trace (once) and hand it to the sink."""
        if self.ended_at is not None:
            return
        self.ended_at = _now()
        self.status = status
        if self.sink is not None:
            self.sink.write(self)

    def __enter__(self) -> Trace:
        self._token = _trace.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _trace.reset(self._token)
            self._token = None
        self.end("error" if exc_type is not None else "ok")


_trace: ContextVar[Trace | None] = ContextVar("repoatlas_trace", default=None)
_span: ContextVar[SpanRecord | None] = ContextVar("repoatlas_span", default=None)


def start_trace(kind: str, snapshot_id: int | None = None, sink: TraceSink | None = None) -> Trace:
    """A new trace; ``with start_trace(...) as trace:`` makes it current until the block ends."""
    return Trace(kind, snapshot_id, sink)


def current_trace() -> Trace | None:
    return _trace.get()


def current_trace_id() -> uuid.UUID | None:
    trace = _trace.get()
    return None if trace is None else trace.id


@contextmanager
def trace_span(name: str, kind: str, **attrs: Any) -> Iterator[Span]:
    """Time one step; nested inside the current span of the current trace."""
    trace = _trace.get()
    parent = _span.get()
    record = SpanRecord(
        id=uuid.uuid4(),
        trace_id=trace.id if trace is not None else uuid.UUID(int=0),
        parent_id=parent.id if parent is not None else None,
        name=name,
        kind=kind,
        started_at=_now(),
        attrs=dict(attrs),
    )
    if trace is not None:
        trace.add(record)
    token = _span.set(record)
    started = time.perf_counter()
    try:
        yield Span(record)
    except BaseException as exc:
        record.status = "error"
        record.attrs["error"] = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
        raise
    else:
        record.status = "ok"
    finally:
        _span.reset(token)
        record.ended_at = _now()
        record.attrs["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
