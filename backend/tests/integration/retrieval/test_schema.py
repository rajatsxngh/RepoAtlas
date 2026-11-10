"""The retrieval schema: chunks with a full-text index, and the shared embeddings cache."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repoatlas.retrieval.models import RETRIEVAL_TABLES

SHA = "a" * 40


def _snapshot(session: Session) -> int:
    repo = session.execute(
        text(
            "INSERT INTO repos (owner, name, default_branch) VALUES ('o', 'r', 'main') RETURNING id"
        )
    ).scalar_one()
    return int(
        session.execute(
            text(
                "INSERT INTO snapshots (repo_id, commit_sha, config_fingerprint) "
                "VALUES (:r, :sha, 'f') RETURNING id"
            ),
            {"r": repo, "sha": SHA},
        ).scalar_one()
    )


def _chunk(session: Session, snapshot: int, **overrides: Any) -> None:
    values = {
        "s": snapshot,
        "node": 1,
        "oid": "b" * 40,
        "path": "a.py",
        "start": 1,
        "end": 3,
        "hash": "h",
        "header": "a.py\nf",
        "body": "def f():\n    return 1\n",
        **overrides,
    }
    session.execute(
        text(
            "INSERT INTO chunks (snapshot_id, node_id, blob_oid, path, start_line, end_line, "
            "chunk_hash, header, text, tsv) VALUES (:s, :node, :oid, :path, :start, :end, "
            ":hash, :header, :body, to_tsvector('simple', :header || ' ' || :body))"
        ),
        values,
    )


def _rejects(session: Session, action: Any) -> bool:
    try:
        with session.begin_nested():
            action()
    except IntegrityError:
        return True
    return False


def test_retrieval_tables_and_indexes_exist(pg_engine: Engine) -> None:
    inspector = inspect(pg_engine)
    assert set(RETRIEVAL_TABLES) <= set(inspector.get_table_names())
    with pg_engine.connect() as connection:
        definitions: dict[str, str] = dict(
            connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'")
            ).all()
        )
    assert "USING gin (tsv)" in definitions["ix_chunks_tsv"]
    assert "(snapshot_id)" in definitions["ix_chunks_snapshot_id"]
    assert {"pk_chunks", "uq_chunks_snapshot_node_start"} <= set(definitions)


def test_chunks_check_their_lines_and_go_with_their_snapshot(db_session: Session) -> None:
    snapshot = _snapshot(db_session)
    _chunk(db_session, snapshot)
    assert _rejects(db_session, lambda: _chunk(db_session, snapshot, start=0, node=2))
    assert _rejects(db_session, lambda: _chunk(db_session, snapshot, start=5, end=4, node=2))
    assert _rejects(db_session, lambda: _chunk(db_session, snapshot))  # same node and start
    found = db_session.execute(
        text("SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('simple', 'return')")
    ).scalar_one()
    assert found == 1
    db_session.execute(text("DELETE FROM snapshots WHERE id = :s"), {"s": snapshot})
    assert db_session.execute(text("SELECT count(*) FROM chunks")).scalar_one() == 0


def test_embeddings_hold_any_dimension_but_must_match_dim(db_session: Session) -> None:
    insert = text(
        "INSERT INTO embeddings (chunk_hash, model, dim, vector) "
        "VALUES (:h, :m, :d, CAST(:v AS vector))"
    )
    db_session.execute(insert, {"h": "h1", "m": "small", "d": 3, "v": "[1,0,0]"})
    db_session.execute(insert, {"h": "h1", "m": "large", "d": 4, "v": "[1,0,0,0]"})
    assert _rejects(
        db_session, lambda: db_session.execute(insert, {"h": "h2", "m": "m", "d": 4, "v": "[1,0]"})
    )
    assert _rejects(
        db_session,
        lambda: db_session.execute(insert, {"h": "h1", "m": "small", "d": 3, "v": "[0,1,0]"}),
    )
    distance = db_session.execute(
        text(
            "SELECT vector <=> CAST('[0,1,0]' AS vector) FROM embeddings "
            "WHERE chunk_hash = 'h1' AND model = 'small'"
        )
    ).scalar_one()
    assert abs(distance - 1.0) < 1e-9
