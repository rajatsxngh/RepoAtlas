"""SQLAlchemy model of impact runs (PRD §7.1, IMP-6).

One ``impact_runs`` row per analysis: the target (a symbol key or a diff), the full report as
JSON, and the trace. Deleting the snapshot deletes its runs; deleting a trace leaves the run
without one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from repoatlas.db.base import Base

IMPACT_TABLES: tuple[str, ...] = ("impact_runs",)


class ImpactRunRow(Base):
    __tablename__ = "impact_runs"
    __table_args__ = (Index("ix_impact_runs_snapshot_id", "snapshot_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("snapshots.id", ondelete="CASCADE")
    )
    target: Mapped[dict[str, Any]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
