"""SQLAlchemy models of traces and spans (PRD §7.1, ANS-5, decision 0014).

A trace is one unit of work a user or an evaluation sees as a whole (an answer, later an impact
run or an eval item); its spans are the steps (retrieval, LLM calls, validation), nested through
``parent_id``. LLM spans carry the model, tokens (``tokens_in`` counts every input token,
cached or not), cache reads, and cost; other numbers go in ``attrs``. A trace's totals are the
sums over its spans, written when it ends. IDs are UUIDs made by the process, so a trace ID is
known before anything is written. Deleting a snapshot deletes its traces; deleting a trace
deletes its spans. ``seq`` numbers a trace's spans in start order (clocks can tie).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from repoatlas.db.base import Base

TRACING_TABLES: tuple[str, ...] = ("traces", "spans")
TraceStatus = Literal["running", "ok", "error"]
TRACE_STATUSES: tuple[str, ...] = get_args(TraceStatus)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(value) for value in values)


class TraceRow(Base):
    __tablename__ = "traces"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(TRACE_STATUSES)})", name="status"),
        Index("ix_traces_snapshot_id", "snapshot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("snapshots.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Double)


class SpanRow(Base):
    __tablename__ = "spans"
    __table_args__ = (
        CheckConstraint(f"status IN ({_in(TRACE_STATUSES)})", name="status"),
        Index("ix_spans_trace_id", "trace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("traces.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    seq: Mapped[int] = mapped_column(Integer)  # start order within the trace
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Double)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB)
