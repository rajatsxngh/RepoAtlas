"""Job queue: background jobs claimed with leases (decision 0007).

Revision ID: 0003_jobs
Revises: 0002_graph_store
Create Date: 2026-11-07

``repoatlas.jobs.models`` says what each column holds. Workers claim queued jobs whose
``run_after`` has come with ``FOR UPDATE SKIP LOCKED`` (``ix_jobs_claim``) and look for running
jobs whose lease ended (``ix_jobs_lease``). At most one queued or running job may hold a given
``dedupe_key`` (``uq_jobs_active_dedupe_key``). The status list is a frozen copy of the queue's
statuses at this revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_jobs"
down_revision: str | None = "0002_graph_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_STATUSES = ("queued", "running", "succeeded", "failed")


def _timestamp(name: str, *, now: bool) -> sa.Column:
    if now:
        return sa.Column(
            name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        )
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    allowed = ", ".join(repr(status) for status in JOB_STATUSES)
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("progress", sa.Double(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        _timestamp("run_after", now=True),
        _timestamp("lease_until", now=False),
        _timestamp("heartbeat_at", now=False),
        sa.Column("error", sa.Text(), nullable=True),
        _timestamp("created_at", now=True),
        _timestamp("updated_at", now=True),
        _timestamp("started_at", now=False),
        _timestamp("finished_at", now=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_jobs_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"status IN ({allowed})", name=op.f("ck_jobs_status")),
        sa.CheckConstraint("progress >= 0 AND progress <= 1", name=op.f("ck_jobs_progress")),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name=op.f("ck_jobs_attempts")),
    )
    op.create_index(
        op.f("ix_jobs_claim"),
        "jobs",
        ["run_after", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        op.f("ix_jobs_lease"),
        "jobs",
        ["lease_until"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(op.f("ix_jobs_snapshot_id"), "jobs", ["snapshot_id"])
    op.create_index(
        op.f("uq_jobs_active_dedupe_key"),
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
