"""Tracing: traces and their spans.

Revision ID: 0005_tracing
Revises: 0004_retrieval
Create Date: 2026-11-12

``repoatlas.tracing.models`` says what each column holds. IDs are UUIDs made by the process.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_tracing"
down_revision: str | None = "0004_retrieval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = "status IN ('running', 'ok', 'error')"


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_traces")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_traces_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(STATUS, name=op.f("ck_traces_status")),
    )
    op.create_index(op.f("ix_traces_snapshot_id"), "traces", ["snapshot_id"])
    op.create_table(
        "spans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Double(), nullable=True),
        sa.Column("attrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spans")),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["traces.id"], name=op.f("fk_spans_trace_id_traces"), ondelete="CASCADE"
        ),
        sa.CheckConstraint(STATUS, name=op.f("ck_spans_status")),
    )
    op.create_index(op.f("ix_spans_trace_id"), "spans", ["trace_id"])


def downgrade() -> None:
    op.drop_table("spans")
    op.drop_table("traces")
