"""SQLAlchemy model of answer runs (PRD §7.1, ANS-11).

One ``qa_runs`` row per question asked: the question, the answering mode, the full result as
JSON (answer, claims, citations, metrics, usage, and the model's draft before validation, kept
for analysis even when the answer was refused), and the numbers worth querying as columns:
whether and why it was refused, pre-filter citation validity, dropped claims, cost, and the
trace. Deleting the snapshot deletes its runs; deleting a trace leaves the run without one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, get_args

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from repoatlas.answers.schema import RefusalKind
from repoatlas.db.base import Base

QA_TABLES: tuple[str, ...] = ("qa_runs",)
REFUSAL_KINDS: tuple[str, ...] = get_args(RefusalKind)


class QaRunRow(Base):
    __tablename__ = "qa_runs"
    __table_args__ = (
        CheckConstraint(
            f"refusal_kind IS NULL OR refusal_kind IN ({', '.join(map(repr, REFUSAL_KINDS))})",
            name="refusal_kind",
        ),
        CheckConstraint("refused = (refusal_kind IS NOT NULL)", name="refused"),
        Index("ix_qa_runs_snapshot_id", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("snapshots.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    refused: Mapped[bool] = mapped_column(Boolean)
    refusal_kind: Mapped[str | None] = mapped_column(Text)
    prefilter_citation_validity: Mapped[float | None] = mapped_column(Double)
    dropped_claims: Mapped[int] = mapped_column(Integer)
    dropped_claim_rate: Mapped[float | None] = mapped_column(Double)
    cost_usd: Mapped[float] = mapped_column(Double)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
