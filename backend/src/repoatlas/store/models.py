"""SQLAlchemy models for the graph store (PRD §7.1, STO-1).

One snapshot is one (repo, commit, config fingerprint). Its files, nodes, edges, unresolved
calls, and owners are written once, in one transaction, and never change afterwards. File text
lives in ``blobs``, keyed by git blob OID and shared by every snapshot that has the same file.

Row IDs (``nodes.id``, ``files.id``) are surrogates inside one snapshot; everything outside the
store names nodes by symbol key. Only ``snapshot_id`` (and ``snapshots.repo_id``) are foreign
keys: references inside a snapshot (``parent_id``, ``src_id``, ``dst_id``, ``caller_id``,
``file_id``) are written together by the loader, and deleting a snapshot cascades through
``snapshot_id`` alone. ``files.blob_oid`` has no foreign key either, because skipped files keep
their OID but have no stored text.

Positions are stored as four columns per range (1-based lines, 0-based UTF-8 byte columns), so a
graph read back from the store equals the graph that was saved. ``meta`` is ``jsonb``, which keeps
every value the graph builder writes exactly; it would turn a float of 1e16 or more (which JSON
writes in exponent form) into an integer, so graph meta should not hold such floats.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, NewType, get_args

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from repoatlas.db.base import Base
from repoatlas.graph.model import EdgeKind, NodeKind, Provenance, UnresolvedReason

SnapshotId = NewType("SnapshotId", int)
SnapshotStatus = Literal["pending", "building", "ready", "failed"]

SNAPSHOT_STATUSES: tuple[str, ...] = get_args(SnapshotStatus)
NODE_KINDS: tuple[str, ...] = tuple(kind.value for kind in NodeKind)
EDGE_KINDS: tuple[str, ...] = tuple(kind.value for kind in EdgeKind)
PROVENANCES: tuple[str, ...] = tuple(provenance.value for provenance in Provenance)
UNRESOLVED_REASONS: tuple[str, ...] = get_args(UnresolvedReason)
DEFAULT_HOST = "github.com"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _snapshot_fk() -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey("snapshots.id", ondelete="CASCADE"))


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class RepoRow(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    host: Mapped[str] = mapped_column(Text, server_default=DEFAULT_HOST)
    owner: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


# GitHub treats owner and repository names case-insensitively.
Index(
    "uq_repos_identity",
    RepoRow.host,
    func.lower(RepoRow.owner),
    func.lower(RepoRow.name),
    unique=True,
)


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint(
            "repo_id", "commit_sha", "config_fingerprint", name="uq_snapshots_identity"
        ),
        CheckConstraint(_one_of("status", SNAPSHOT_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    repo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repos.id", ondelete="CASCADE"))
    commit_sha: Mapped[str] = mapped_column(Text)
    config_fingerprint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class BlobRow(Base):
    """One git blob's text. ``lossy``: the bytes were not valid UTF-8 (or held NUL characters,
    which Postgres text cannot store) and were replaced with U+FFFD; line breaks are kept, so
    line numbers still match the original."""

    __tablename__ = "blobs"

    oid: Mapped[str] = mapped_column(Text, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    line_count: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    lossy: Mapped[bool] = mapped_column(Boolean, server_default=false())


class FileRow(Base):
    """Every inventoried file of a snapshot; skipped files keep their reason and have no text."""

    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("snapshot_id", "path", name="uq_files_snapshot_path"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = _snapshot_fk()
    path: Mapped[str] = mapped_column(Text)
    blob_oid: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    language: Mapped[str] = mapped_column(Text)
    loc: Mapped[int | None] = mapped_column(Integer)
    is_test: Mapped[bool] = mapped_column(Boolean)
    skip_reason: Mapped[str | None] = mapped_column(Text)


class NodeRow(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "symbol_key", name="uq_nodes_snapshot_key"),
        CheckConstraint(_one_of("kind", NODE_KINDS), name="kind"),
        Index("ix_nodes_file_id", "file_id"),
        Index(
            "ix_nodes_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_nodes_qualified_name_trgm",
            "qualified_name",
            postgresql_using="gin",
            postgresql_ops={"qualified_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = _snapshot_fk()
    symbol_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    qualified_name: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    file_id: Mapped[int | None] = mapped_column(BigInteger)  # None for dir nodes
    name_start_line: Mapped[int | None] = mapped_column(Integer)
    name_start_col: Mapped[int | None] = mapped_column(Integer)
    name_end_line: Mapped[int | None] = mapped_column(Integer)
    name_end_col: Mapped[int | None] = mapped_column(Integer)
    span_start_line: Mapped[int | None] = mapped_column(Integer)
    span_start_col: Mapped[int | None] = mapped_column(Integer)
    span_end_line: Mapped[int | None] = mapped_column(Integer)
    span_end_col: Mapped[int | None] = mapped_column(Integer)
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    exported: Mapped[bool] = mapped_column(Boolean, server_default=false())
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class EdgeRow(Base):
    __tablename__ = "edges"
    __table_args__ = (
        CheckConstraint(_one_of("kind", EDGE_KINDS), name="kind"),
        CheckConstraint(_one_of("provenance", PROVENANCES), name="provenance"),
        CheckConstraint("confidence > 0 AND confidence <= 1", name="confidence"),
        Index("ix_edges_snapshot_src", "snapshot_id", "src_id"),
        Index("ix_edges_snapshot_dst", "snapshot_id", "dst_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = _snapshot_fk()
    src_id: Mapped[int] = mapped_column(BigInteger)
    dst_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Double)
    site_start_line: Mapped[int | None] = mapped_column(Integer)
    site_start_col: Mapped[int | None] = mapped_column(Integer)
    site_end_line: Mapped[int | None] = mapped_column(Integer)
    site_end_col: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class UnresolvedCallRow(Base):
    """A call site with no edge. ``callee_name`` is the last identifier of ``callee_text``
    (``append`` for ``self.items.append``), or ``None`` when the callee is not a name."""

    __tablename__ = "unresolved_calls"
    __table_args__ = (
        CheckConstraint(_one_of("reason", UNRESOLVED_REASONS), name="reason"),
        Index("ix_unresolved_calls_snapshot_name", "snapshot_id", "callee_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = _snapshot_fk()
    caller_id: Mapped[int] = mapped_column(BigInteger)
    callee_text: Mapped[str] = mapped_column(Text)
    callee_name: Mapped[str | None] = mapped_column(Text)
    receiver_text: Mapped[str | None] = mapped_column(Text)
    site_start_line: Mapped[int] = mapped_column(Integer)
    site_start_col: Mapped[int] = mapped_column(Integer)
    site_end_line: Mapped[int] = mapped_column(Integer)
    site_end_col: Mapped[int] = mapped_column(Integer)
    candidates: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    reason: Mapped[str] = mapped_column(Text)


class OwnerRow(Base):
    """One author's share of a file's commits; emails are only ever stored hashed."""

    __tablename__ = "owners"
    __table_args__ = (PrimaryKeyConstraint("snapshot_id", "file_id", "author_hash"),)

    snapshot_id: Mapped[int] = _snapshot_fk()
    file_id: Mapped[int] = mapped_column(BigInteger)
    author_hash: Mapped[str] = mapped_column(Text)
    author_name: Mapped[str] = mapped_column(Text)
    commits: Mapped[int] = mapped_column(Integer)
    last_commit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    share: Mapped[float] = mapped_column(Double)


STORE_TABLES: tuple[str, ...] = (
    "repos",
    "snapshots",
    "blobs",
    "files",
    "nodes",
    "edges",
    "unresolved_calls",
    "owners",
)
