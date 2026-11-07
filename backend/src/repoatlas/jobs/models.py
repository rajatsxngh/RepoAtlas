"""SQLAlchemy model of the job queue (PRD §7.1, decision 0007).

A job moves ``queued → running → succeeded | failed``. A worker claims a queued job whose
``run_after`` has come, which makes it ``running`` with ``worker_id`` and a lease
(``lease_until``) that its heartbeats extend. ``attempts`` counts the attempts that started;
together with ``worker_id`` it fences every later write, so a worker whose lease ran out can no
longer change the job. A failed attempt, or a lease that ran out, puts the job back to
``queued`` with ``run_after`` pushed back, until ``max_attempts`` attempts have been made.

``stage`` and ``progress`` (0 to 1) show how far the current attempt has got. ``snapshot_id``
ties a job to the snapshot it builds (deleting the snapshot deletes its jobs), and at most one
queued or running job may hold a given ``dedupe_key``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from repoatlas.db.base import Base

JobStatus = Literal["queued", "running", "succeeded", "failed"]
JOB_STATUSES: tuple[str, ...] = get_args(JobStatus)
ACTIVE_STATUSES: tuple[str, ...] = ("queued", "running")


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(status) for status in JOB_STATUSES)})", name="status"
        ),
        CheckConstraint("progress >= 0 AND progress <= 1", name="progress"),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempts"),
        Index("ix_jobs_claim", "run_after", "id", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_lease", "lease_until", postgresql_where=text("status = 'running'")),
        Index("ix_jobs_snapshot_id", "snapshot_id"),
        Index(
            "uq_jobs_active_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    stage: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(Double, server_default=text("0"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("snapshots.id", ondelete="CASCADE")
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
