"""Graph store: repos, snapshots, blobs, files, nodes, edges, unresolved calls, owners.

Revision ID: 0002_graph_store
Revises: 0001_baseline
Create Date: 2026-11-07

Tables and indexes as in PRD §7.1 (``repoatlas.store.models`` says what each column holds). The
value lists below are frozen copies of the graph vocabulary at this revision; a new node kind,
edge kind, or provenance needs a migration that widens the check constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_graph_store"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOT_STATUSES = ("pending", "building", "ready", "failed")
NODE_KINDS = ("dir", "file", "class", "function", "method", "route", "test")
EDGE_KINDS = ("contains", "imports", "calls", "inherits", "tested_by", "routes_to")
PROVENANCES = ("tree_sitter", "import_resolver", "name_match", "convention", "git")
UNRESOLVED_REASONS = ("ambiguous", "not_found", "unbound_name", "dynamic")
TABLES = ("repos", "snapshots", "blobs", "files", "nodes", "edges", "unresolved_calls", "owners")


def _one_of(table: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    allowed = ", ".join(repr(value) for value in values)
    return sa.CheckConstraint(f"{column} IN ({allowed})", name=op.f(f"ck_{table}_{column}"))


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False)


def _pk(table: str, *columns: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint(*(columns or ("id",)), name=op.f(f"pk_{table}"))


def _fk(table: str, column: str, referred: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{referred}.id"],
        name=op.f(f"fk_{table}_{column}_{referred}"),
        ondelete="CASCADE",
    )


def _snapshot(table: str) -> list[sa.Column | sa.ForeignKeyConstraint]:
    return [
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        _fk(table, "snapshot_id", "snapshots"),
    ]


def _range(prefix: str, *, nullable: bool) -> list[sa.Column]:
    return [
        sa.Column(f"{prefix}_{part}", sa.Integer(), nullable=nullable)
        for part in ("start_line", "start_col", "end_line", "end_col")
    ]


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _jsonb(name: str) -> sa.Column:
    return sa.Column(
        name, postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "repos",
        _id(),
        sa.Column("host", sa.Text(), server_default="github.com", nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=False),
        _now("created_at"),
        _pk("repos"),
    )
    # GitHub treats owner and repository names case-insensitively.
    op.create_index(
        op.f("uq_repos_identity"),
        "repos",
        ["host", sa.text("lower(owner)"), sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "snapshots",
        _id(),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("config_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        _jsonb("stats"),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        _pk("snapshots"),
        _fk("snapshots", "repo_id", "repos"),
        sa.UniqueConstraint(
            "repo_id",
            "commit_sha",
            "config_fingerprint",
            name=op.f("uq_snapshots_identity"),
        ),
        _one_of("snapshots", "status", SNAPSHOT_STATUSES),
    )

    op.create_table(
        "blobs",
        sa.Column("oid", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("lossy", sa.Boolean(), server_default=sa.false(), nullable=False),
        _pk("blobs", "oid"),
    )

    op.create_table(
        "files",
        _id(),
        *_snapshot("files"),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("blob_oid", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("loc", sa.Integer(), nullable=True),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        _pk("files"),
        sa.UniqueConstraint("snapshot_id", "path", name=op.f("uq_files_snapshot_path")),
    )

    op.create_table(
        "nodes",
        _id(),
        *_snapshot("nodes"),
        sa.Column("symbol_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        *_range("name", nullable=True),
        *_range("span", nullable=True),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("exported", sa.Boolean(), server_default=sa.false(), nullable=False),
        _jsonb("meta"),
        _pk("nodes"),
        sa.UniqueConstraint("snapshot_id", "symbol_key", name=op.f("uq_nodes_snapshot_key")),
        _one_of("nodes", "kind", NODE_KINDS),
    )
    op.create_index(op.f("ix_nodes_file_id"), "nodes", ["file_id"])
    for column in ("name", "qualified_name"):
        op.create_index(
            op.f(f"ix_nodes_{column}_trgm"),
            "nodes",
            [column],
            postgresql_using="gin",
            postgresql_ops={column: "gin_trgm_ops"},
        )

    op.create_table(
        "edges",
        _id(),
        *_snapshot("edges"),
        sa.Column("src_id", sa.BigInteger(), nullable=False),
        sa.Column("dst_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        *_range("site", nullable=True),
        _jsonb("meta"),
        _pk("edges"),
        _one_of("edges", "kind", EDGE_KINDS),
        _one_of("edges", "provenance", PROVENANCES),
        sa.CheckConstraint("confidence > 0 AND confidence <= 1", name=op.f("ck_edges_confidence")),
    )
    op.create_index(op.f("ix_edges_snapshot_src"), "edges", ["snapshot_id", "src_id"])
    op.create_index(op.f("ix_edges_snapshot_dst"), "edges", ["snapshot_id", "dst_id"])

    op.create_table(
        "unresolved_calls",
        _id(),
        *_snapshot("unresolved_calls"),
        sa.Column("caller_id", sa.BigInteger(), nullable=False),
        sa.Column("callee_text", sa.Text(), nullable=False),
        sa.Column("callee_name", sa.Text(), nullable=True),
        sa.Column("receiver_text", sa.Text(), nullable=True),
        *_range("site", nullable=False),
        sa.Column(
            "candidates",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        _pk("unresolved_calls"),
        _one_of("unresolved_calls", "reason", UNRESOLVED_REASONS),
    )
    op.create_index(
        op.f("ix_unresolved_calls_snapshot_name"),
        "unresolved_calls",
        ["snapshot_id", "callee_name"],
    )

    op.create_table(
        "owners",
        *_snapshot("owners"),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("author_hash", sa.Text(), nullable=False),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("commits", sa.Integer(), nullable=False),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("share", sa.Double(), nullable=False),
        _pk("owners", "snapshot_id", "file_id", "author_hash"),
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
