"""Incremental builds: a parse cache shared across snapshots, and per-file build records.

Revision ID: 0009_incremental_builds
Revises: 0008_spans_llm_started_at
Create Date: 2026-11-15

``parse_cache`` holds one parse result per (path, blob, parser version), as the JSON text of a
``ParsedFile``; like ``blobs`` and ``embeddings`` it belongs to no snapshot. ``file_records``
holds what a build keeps about each parsed file of a snapshot for the next build of the
repository (decision 0024). Both are new tables that code of the previous release never
reads, so a rollback needs no downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_incremental_builds"
down_revision: str | None = "0008_spans_llm_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parse_cache",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("blob_oid", sa.Text(), nullable=False),
        sa.Column("parsed", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_parse_cache")),
    )
    op.create_table(
        "file_records",
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("imports_digest", sa.Text(), nullable=False),
        sa.Column(
            "calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "path", name=op.f("pk_file_records")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_file_records_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("file_records")
    op.drop_table("parse_cache")
