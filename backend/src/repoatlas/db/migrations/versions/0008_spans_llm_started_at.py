"""An index for the daily spend total.

Revision ID: 0008_spans_llm_started_at
Revises: 0007_impact_runs
Create Date: 2026-11-14

``repoatlas.limits.SpendGuard`` sums the cost of ``llm`` spans started since midnight UTC before
LLM calls; this partial index keeps that a range scan over the day's LLM spans. Adding an index
changes nothing for code of the previous release, so a rollback needs no downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_spans_llm_started_at"
down_revision: str | None = "0007_impact_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_spans_llm_started_at",
        "spans",
        ["started_at"],
        unique=False,
        postgresql_where=sa.text("kind = 'llm'"),
    )


def downgrade() -> None:
    op.drop_index("ix_spans_llm_started_at", table_name="spans")
