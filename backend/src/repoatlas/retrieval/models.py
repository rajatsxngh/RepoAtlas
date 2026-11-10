"""SQLAlchemy models for retrieval: chunks and their embeddings (PRD §7.1, RET-2, RET-3).

``chunks`` belong to one snapshot: one row per searchable piece of a symbol (or of a file's
top-level code), with the evidence reference fields (``blob_oid``, ``path``, ``start_line``,
``end_line``), the ``header`` that is prepended for embedding and keyword search, the chunk
``text``, and a ``tsv`` for full-text search. ``node_id`` is the row of the node the chunk
belongs to (a file node for module chunks); like other references inside a snapshot it has no
foreign key, and deleting the snapshot deletes its chunks.

``embeddings`` are shared by every snapshot: one vector per (``chunk_hash``, ``model``), so a
chunk whose header and text did not change is never embedded again. ``vector`` is an untyped
pgvector column (any dimension; ``dim`` says which, and a check keeps the two equal), because
different models give different dimensions.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from repoatlas.db.base import Base

RETRIEVAL_TABLES: tuple[str, ...] = ("chunks", "embeddings")


class ChunkRow(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "node_id", "start_line", name="uq_chunks_snapshot_node_start"
        ),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="lines"),
        Index("ix_chunks_snapshot_id", "snapshot_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("snapshots.id", ondelete="CASCADE")
    )
    node_id: Mapped[int] = mapped_column(BigInteger)
    blob_oid: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    chunk_hash: Mapped[str] = mapped_column(Text)
    header: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    tsv: Mapped[str] = mapped_column(TSVECTOR)


class EmbeddingRow(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        PrimaryKeyConstraint("chunk_hash", "model"),
        CheckConstraint("dim > 0 AND vector_dims(vector) = dim", name="dim"),
    )

    chunk_hash: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
