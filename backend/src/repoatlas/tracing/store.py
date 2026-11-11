"""Writing traces to Postgres and reading them back (``GET /traces/{id}``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine, insert, select

from repoatlas.tracing.core import Trace
from repoatlas.tracing.models import SpanRow, TraceRow, TraceStatus


class SqlTraceSink:
    """Writes an ended trace and all its spans in one transaction."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def write(self, trace: Trace) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(TraceRow).values(
                    id=trace.id,
                    kind=trace.kind,
                    snapshot_id=trace.snapshot_id,
                    started_at=trace.started_at,
                    ended_at=trace.ended_at,
                    status=trace.status,
                    tokens_in=trace.tokens_in,
                    tokens_out=trace.tokens_out,
                    cost_usd=trace.cost_usd,
                )
            )
            if trace.spans:
                connection.execute(
                    insert(SpanRow),
                    [
                        {
                            "id": span.id,
                            "trace_id": trace.id,
                            "parent_id": span.parent_id,
                            "seq": seq,
                            "name": span.name,
                            "kind": span.kind,
                            "model": span.model,
                            # A span still open when its trace ends (it shouldn't be) is an error.
                            "status": span.status if span.status != "running" else "error",
                            "started_at": span.started_at,
                            "ended_at": span.ended_at,
                            "tokens_in": span.tokens_in,
                            "tokens_out": span.tokens_out,
                            "cache_read_tokens": span.cache_read_tokens,
                            "cost_usd": span.cost_usd,
                            "attrs": span.attrs,
                        }
                        for seq, span in enumerate(trace.spans)
                    ],
                )


class SpanView(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    kind: str
    model: str | None
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None
    tokens_in: int | None
    tokens_out: int | None
    cache_read_tokens: int | None
    cost_usd: float | None
    attrs: dict[str, Any]


class TraceView(BaseModel):
    id: uuid.UUID
    kind: str
    snapshot_id: int | None
    started_at: datetime
    ended_at: datetime | None
    status: TraceStatus
    tokens_in: int
    tokens_out: int
    cost_usd: float
    spans: list[SpanView]


def load_trace(engine: Engine, trace_id: uuid.UUID) -> TraceView | None:
    """The trace with its spans in start order, or ``None``."""
    with engine.connect() as connection:
        row = connection.execute(select(TraceRow).where(TraceRow.id == trace_id)).first()
        if row is None:
            return None
        spans = connection.execute(
            select(SpanRow).where(SpanRow.trace_id == trace_id).order_by(SpanRow.seq)
        ).all()
    return TraceView.model_validate(
        {
            **row._mapping,
            "spans": [SpanView.model_validate(span, from_attributes=True) for span in spans],
        }
    )
