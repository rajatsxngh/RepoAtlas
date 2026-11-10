"""The graph store schema: tables, indexes, constraints, and models that match migrations."""

from __future__ import annotations

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import repoatlas.jobs.models
import repoatlas.retrieval.models  # noqa: F401  (so are the retrieval tables)
from repoatlas.db import migrate
from repoatlas.db.base import Base
from repoatlas.db.engine import create_db_engine
from repoatlas.store.models import (
    EDGE_KINDS,
    NODE_KINDS,
    PROVENANCES,
    SNAPSHOT_STATUSES,
    STORE_TABLES,
    UNRESOLVED_REASONS,
)

SHA = "a" * 40
EXPECTED_INDEXES = {
    "repos": {"pk_repos", "uq_repos_identity"},
    "snapshots": {"pk_snapshots", "uq_snapshots_identity"},
    "blobs": {"pk_blobs"},
    "files": {"pk_files", "uq_files_snapshot_path"},
    "nodes": {
        "pk_nodes",
        "uq_nodes_snapshot_key",
        "ix_nodes_file_id",
        "ix_nodes_name_trgm",
        "ix_nodes_qualified_name_trgm",
    },
    "edges": {"pk_edges", "ix_edges_snapshot_src", "ix_edges_snapshot_dst"},
    "unresolved_calls": {"pk_unresolved_calls", "ix_unresolved_calls_snapshot_name"},
    "owners": {"pk_owners"},
}


def _insert(session: Session, table: str, **values: Any) -> int:
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    returning = "" if table == "owners" else " RETURNING " + ("oid" if table == "blobs" else "id")
    result = session.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({params}){returning}"), values
    )
    return result.scalar_one() if returning else 0


def _snapshot(session: Session, owner: str = "pallets", name: str = "flask") -> int:
    repo = _insert(session, "repos", owner=owner, name=name, default_branch="main")
    return _insert(session, "snapshots", repo_id=repo, commit_sha=SHA, config_fingerprint="f" * 64)


def _node(session: Session, snapshot: int, key: str, kind: str = "function") -> int:
    return _insert(
        session,
        "nodes",
        snapshot_id=snapshot,
        symbol_key=key,
        kind=kind,
        name=key,
        qualified_name=key,
        path="src/app.py",
    )


def _rejects(session: Session, statement: str, params: dict[str, Any]) -> bool:
    try:
        with session.begin_nested():
            session.execute(text(statement), params)
    except IntegrityError:
        return True
    return False


def test_store_tables_exist(pg_engine: Engine) -> None:
    assert set(STORE_TABLES) <= set(inspect(pg_engine).get_table_names())


def test_models_match_the_migrations(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    assert differences == []


def test_indexes(pg_engine: Engine) -> None:
    with pg_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            )
        ).all()
    found: dict[str, set[str]] = {}
    definitions = {}
    for table, index, definition in rows:
        found.setdefault(table, set()).add(index)
        definitions[index] = definition
    for table, expected in EXPECTED_INDEXES.items():
        assert found[table] == expected, table
    assert "lower(owner)" in definitions["uq_repos_identity"]
    for column in ("name", "qualified_name"):
        assert f"gin ({column} gin_trgm_ops)" in definitions[f"ix_nodes_{column}_trgm"]
    assert "(snapshot_id, src_id)" in definitions["ix_edges_snapshot_src"]
    assert "(snapshot_id, dst_id)" in definitions["ix_edges_snapshot_dst"]


def test_vocabulary_constraints_accept_every_value_and_nothing_else(db_session: Session) -> None:
    snapshot = _snapshot(db_session)
    for kind in NODE_KINDS:
        _node(db_session, snapshot, f"key:{kind}", kind)
    src, dst = _node(db_session, snapshot, "src"), _node(db_session, snapshot, "dst")
    edge = (
        "INSERT INTO edges (snapshot_id, src_id, dst_id, kind, provenance, confidence) "
        "VALUES (:s, :a, :b, :kind, :provenance, :confidence)"
    )
    for kind in EDGE_KINDS:
        for provenance in PROVENANCES:
            row = {"s": snapshot, "a": src, "b": dst, "kind": kind, "provenance": provenance}
            assert not _rejects(db_session, edge, {**row, "confidence": 1.0})
    ok = {"s": snapshot, "a": src, "b": dst, "kind": "calls", "provenance": "name_match"}
    assert _rejects(db_session, edge, {**ok, "kind": "uses", "confidence": 0.5})
    assert _rejects(db_session, edge, {**ok, "provenance": "llm", "confidence": 0.5})
    assert _rejects(db_session, edge, {**ok, "confidence": 0.0})
    assert _rejects(db_session, edge, {**ok, "confidence": 1.5})
    node = (
        "INSERT INTO nodes (snapshot_id, symbol_key, kind, name, qualified_name, path) "
        "VALUES (:s, :key, :kind, 'x', 'x', 'x.py')"
    )
    assert _rejects(db_session, node, {"s": snapshot, "key": "bad", "kind": "module"})

    call = (
        "INSERT INTO unresolved_calls (snapshot_id, caller_id, callee_text, site_start_line, "
        "site_start_col, site_end_line, site_end_col, reason) "
        "VALUES (:s, :caller, 'f', 1, 0, 1, 3, :reason)"
    )
    for reason in UNRESOLVED_REASONS:
        assert not _rejects(db_session, call, {"s": snapshot, "caller": src, "reason": reason})
    assert _rejects(db_session, call, {"s": snapshot, "caller": src, "reason": "unknown"})

    status = "UPDATE snapshots SET status = :status WHERE id = :s"
    for value in SNAPSHOT_STATUSES:
        assert not _rejects(db_session, status, {"s": snapshot, "status": value})
    assert _rejects(db_session, status, {"s": snapshot, "status": "done"})


def test_new_snapshots_are_pending_with_empty_stats(db_session: Session) -> None:
    snapshot = _snapshot(db_session)
    row = db_session.execute(
        text("SELECT status, stats, error, created_at, updated_at FROM snapshots WHERE id = :s"),
        {"s": snapshot},
    ).one()
    assert (row.status, row.stats, row.error) == ("pending", {}, None)
    assert row.created_at.tzinfo is not None and row.updated_at.tzinfo is not None


def test_identity_constraints(db_session: Session) -> None:
    snapshot = _snapshot(db_session)
    repo_insert = "INSERT INTO repos (owner, name, default_branch) VALUES (:owner, :name, 'main')"
    assert _rejects(db_session, repo_insert, {"owner": "Pallets", "name": "FLASK"})
    assert not _rejects(db_session, repo_insert, {"owner": "pallets", "name": "click"})
    repo = db_session.execute(text("SELECT repo_id FROM snapshots WHERE id = :s"), {"s": snapshot})
    snapshot_insert = (
        "INSERT INTO snapshots (repo_id, commit_sha, config_fingerprint) VALUES (:r, :sha, :fp)"
    )
    params = {"r": repo.scalar_one(), "sha": SHA, "fp": "f" * 64}
    assert _rejects(db_session, snapshot_insert, params)
    assert not _rejects(db_session, snapshot_insert, {**params, "fp": "e" * 64})

    _node(db_session, snapshot, "py:a.py:f:function")
    node = (
        "INSERT INTO nodes (snapshot_id, symbol_key, kind, name, qualified_name, path) "
        "VALUES (:s, 'py:a.py:f:function', 'function', 'f', 'f', 'a.py')"
    )
    assert _rejects(db_session, node, {"s": snapshot})
    other = _snapshot(db_session, "pallets", "werkzeug")
    assert not _rejects(db_session, node, {"s": other})

    file = (
        "INSERT INTO files (snapshot_id, path, blob_oid, mode, language, is_test) "
        "VALUES (:s, 'a.py', :oid, '100644', 'py', false)"
    )
    assert not _rejects(db_session, file, {"s": snapshot, "oid": "b" * 40})
    assert _rejects(db_session, file, {"s": snapshot, "oid": "c" * 40})


def test_deleting_a_snapshot_deletes_its_rows_but_keeps_blobs(db_session: Session) -> None:
    snapshot = _snapshot(db_session)
    _insert(db_session, "blobs", oid="b" * 40, size_bytes=2, line_count=1, text="x\n")
    file = _insert(
        db_session,
        "files",
        snapshot_id=snapshot,
        path="a.py",
        blob_oid="b" * 40,
        mode="100644",
        language="py",
        is_test=False,
    )
    src, dst = _node(db_session, snapshot, "a"), _node(db_session, snapshot, "b")
    _insert(
        db_session,
        "edges",
        snapshot_id=snapshot,
        src_id=src,
        dst_id=dst,
        kind="calls",
        provenance="name_match",
        confidence=0.95,
    )
    _insert(
        db_session,
        "owners",
        snapshot_id=snapshot,
        file_id=file,
        author_hash="0" * 16,
        author_name="Ada",
        commits=1,
        last_commit_at="2026-01-01T00:00:00Z",
        share=1.0,
    )
    db_session.execute(text("DELETE FROM snapshots WHERE id = :s"), {"s": snapshot})
    for table in ("files", "nodes", "edges", "unresolved_calls", "owners"):
        count = db_session.execute(
            text(f"SELECT count(*) FROM {table} WHERE snapshot_id = :s"), {"s": snapshot}
        ).scalar_one()
        assert count == 0, table
    assert db_session.execute(text("SELECT count(*) FROM blobs")).scalar_one() == 1


def test_downgrade_and_upgrade_again(scratch_db_url: str) -> None:
    migrate.upgrade(scratch_db_url)
    command.downgrade(migrate.alembic_config(scratch_db_url), "0001_baseline")
    assert migrate.current_revision(scratch_db_url) == "0001_baseline"
    engine = create_db_engine(scratch_db_url)
    try:
        assert not set(STORE_TABLES) & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    migrate.upgrade(scratch_db_url)
    assert migrate.current_revision(scratch_db_url) == migrate.head_revision()


@pytest.mark.parametrize("table", STORE_TABLES)
def test_every_store_table_is_mapped(table: str) -> None:
    assert table in Base.metadata.tables
