"""Retrieval: chunks with a full-text index, and embeddings shared across snapshots.

Revision ID: 0004_retrieval
Revises: 0003_jobs
Create Date: 2026-11-11

``repoatlas.retrieval.models`` says what each column holds. ``embeddings.vector`` is an untyped
pgvector column, so one table holds every model's vectors; no approximate index (decision 0009).
The snapshot columns ``embedding_model`` and ``embedding_dim`` already exist (0002).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0004_retrieval"
down_revision: str | None = "0003_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.BigInteger(), nullable=False),
        sa.Column("blob_oid", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("chunk_hash", sa.Text(), nullable=False),
        sa.Column("header", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_chunks_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "snapshot_id", "node_id", "start_line", name=op.f("uq_chunks_snapshot_node_start")
        ),
        sa.CheckConstraint(
            "start_line >= 1 AND end_line >= start_line", name=op.f("ck_chunks_lines")
        ),
    )
    op.create_index(op.f("ix_chunks_snapshot_id"), "chunks", ["snapshot_id"])
    op.create_index(op.f("ix_chunks_tsv"), "chunks", ["tsv"], postgresql_using="gin")
    op.create_table(
        "embeddings",
        sa.Column("chunk_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", Vector(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("chunk_hash", "model", name=op.f("pk_embeddings")),
        sa.CheckConstraint("dim > 0 AND vector_dims(vector) = dim", name=op.f("ck_embeddings_dim")),
    )


def downgrade() -> None:
    op.drop_table("embeddings")
    op.drop_table("chunks")
