"""Impact runs.

Revision ID: 0007_impact_runs
Revises: 0006_qa_runs
Create Date: 2026-11-13

``repoatlas.impact.models`` says what each column holds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_impact_runs"
down_revision: str | None = "0006_qa_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "impact_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impact_runs")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_impact_runs_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["traces.id"],
            name=op.f("fk_impact_runs_trace_id_traces"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_impact_runs_snapshot_id"), "impact_runs", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("impact_runs")
