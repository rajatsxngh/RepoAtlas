"""Answer runs.

Revision ID: 0006_qa_runs
Revises: 0005_tracing
Create Date: 2026-11-12

``repoatlas.answers.models`` says what each column holds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_qa_runs"
down_revision: str | None = "0005_tracing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("refused", sa.Boolean(), nullable=False),
        sa.Column("refusal_kind", sa.Text(), nullable=True),
        sa.Column("prefilter_citation_validity", sa.Double(), nullable=True),
        sa.Column("dropped_claims", sa.Integer(), nullable=False),
        sa.Column("dropped_claim_rate", sa.Double(), nullable=True),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_runs")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_qa_runs_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["traces.id"],
            name=op.f("fk_qa_runs_trace_id_traces"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "refusal_kind IS NULL OR refusal_kind IN ('evidence', 'api', 'budget')",
            name=op.f("ck_qa_runs_refusal_kind"),
        ),
        sa.CheckConstraint("refused = (refusal_kind IS NOT NULL)", name=op.f("ck_qa_runs_refused")),
    )
    op.create_index(op.f("ix_qa_runs_snapshot_id"), "qa_runs", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("qa_runs")
