"""The launch gallery: which precomputed snapshots the landing page shows.

Revision ID: 0010_gallery
Revises: 0009_incremental_builds
Create Date: 2026-11-16

``gallery`` holds one row per repository of the gallery list, written by
``repoatlas precompute`` (decision 0025). A row points at the snapshot built for the pinned
commit and goes away with it. It is a new table that code of the previous release never
reads, so a rollback needs no downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_gallery"
down_revision: str | None = "0009_incremental_builds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gallery",
        sa.Column("repo_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("benchmark", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("repo_key", name=op.f("pk_gallery")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_gallery_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("gallery")
