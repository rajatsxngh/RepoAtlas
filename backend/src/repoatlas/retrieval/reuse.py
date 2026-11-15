"""Chunks of an incremental build (PRF-2, PRF-3, decision 0024): copy what did not change.

A file's chunks depend on its text and on its own nodes, nothing else. So a file with the same
path and blob as in the previous snapshot of the repository (same config fingerprint, hence
the same parser and chunker) has the same chunks, and they are copied row by row instead of
being cut again; only their node IDs change, found by symbol key.

The copied and the newly cut chunks are inserted together, in path order, so chunk IDs rise in
the same order as in a full build. Search breaks ties by chunk ID, so this keeps an incremental
snapshot's results identical to a full rebuild's.

``chunk_stats`` counts a snapshot's chunks from its rows, so a build that copied chunks
reports the same numbers as one that cut them all.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import Any

from sqlalchemy import Engine, text

from repoatlas.retrieval.chunker import CHARS_PER_TOKEN, MAX_CHUNK_TOKENS, Chunk
from repoatlas.retrieval.keyword import chunk_tsvector

INSERT_BATCH = 500
_COLUMNS = "blob_oid, path, start_line, end_line, chunk_hash, header, text, tsv"


def reusable_paths(snapshot_id: int, previous_id: int, *, engine: Engine) -> set[str]:
    """Indexed files of ``snapshot_id`` whose chunks can be copied from ``previous_id``: the
    same path and blob there, and every chunk's node found again by its symbol key. (A chunk
    whose node is missing would mean the file's nodes differ; the file is cut again instead.)"""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT f1.path, count(c.id) AS chunks, count(n2.id) AS mapped "
                "FROM files f1 "
                "JOIN files f2 ON f2.snapshot_id = :new AND f2.path = f1.path "
                "AND f2.blob_oid = f1.blob_oid AND f2.skip_reason IS NULL "
                "LEFT JOIN chunks c ON c.snapshot_id = :prev AND c.path = f1.path "
                "LEFT JOIN nodes n1 ON n1.id = c.node_id "
                "LEFT JOIN nodes n2 ON n2.snapshot_id = :new AND n2.symbol_key = n1.symbol_key "
                "WHERE f1.snapshot_id = :prev AND f1.skip_reason IS NULL "
                "GROUP BY f1.path"
            ),
            {"new": snapshot_id, "prev": previous_id},
        ).all()
    return {row.path for row in rows if row.chunks == row.mapped}


def save_chunks_reusing(
    snapshot_id: int,
    previous_id: int,
    reused: Collection[str],
    chunks: Sequence[Chunk],
    *,
    engine: Engine,
    keyword_document: Callable[[Chunk], str] = chunk_tsvector,
) -> int:
    """Replace the snapshot's chunks with the previous snapshot's chunks of the ``reused``
    paths plus ``chunks`` (the other files', newly cut), in one transaction. Returns how many
    were copied."""
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM chunks WHERE snapshot_id = :s"), {"s": snapshot_id})
        connection.execute(
            text(
                "CREATE TEMPORARY TABLE chunks_incoming (seq integer, node_id bigint, "
                "blob_oid text, path text, start_line integer, end_line integer, "
                "chunk_hash text, header text, text text, tsv tsvector) ON COMMIT DROP"
            )
        )
        insert = text(
            "INSERT INTO chunks_incoming (seq, node_id, blob_oid, path, start_line, end_line, "
            "chunk_hash, header, text, tsv) VALUES (:seq, :node_id, :blob_oid, :path, "
            ":start_line, :end_line, :chunk_hash, :header, :text, CAST(:doc AS tsvector))"
        )
        for offset in range(0, len(chunks), INSERT_BATCH):
            connection.execute(
                insert,
                [
                    {
                        "seq": offset + index,
                        "node_id": chunk.node_id,
                        "blob_oid": chunk.blob_oid,
                        "path": chunk.path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "chunk_hash": chunk.chunk_hash,
                        "header": chunk.header,
                        "text": chunk.text,
                        "doc": keyword_document(chunk),
                    }
                    for index, chunk in enumerate(chunks[offset : offset + INSERT_BATCH])
                ],
            )
        # One insert for both, ordered like a full build: by path, then each file's chunks in
        # the order they were made (the previous snapshot's ID order, or this build's).
        connection.execute(
            text(
                f"INSERT INTO chunks (snapshot_id, node_id, {_COLUMNS}) "
                f"SELECT :new, merged.node_id, {_COLUMNS} FROM ("
                f"SELECT n2.id AS node_id, c.id AS seq, c.blob_oid, c.path, c.start_line, "
                "c.end_line, c.chunk_hash, c.header, c.text, c.tsv FROM chunks c "
                "JOIN nodes n1 ON n1.id = c.node_id "
                "JOIN nodes n2 ON n2.snapshot_id = :new AND n2.symbol_key = n1.symbol_key "
                "WHERE c.snapshot_id = :prev AND c.path = ANY(:reused) "
                "UNION ALL "
                f"SELECT node_id, seq, {_COLUMNS} FROM chunks_incoming"
                ") merged ORDER BY merged.path, merged.seq"
            ),
            {"new": snapshot_id, "prev": previous_id, "reused": sorted(reused)},
        )
        total = connection.execute(
            text("SELECT count(*) FROM chunks WHERE snapshot_id = :s"), {"s": snapshot_id}
        ).scalar_one()
    return int(total) - len(chunks)


def chunk_stats(snapshot_id: int, *, engine: Engine) -> dict[str, Any]:
    """Counts of a snapshot's chunks, from its rows: how many, by kind, how many are parts of
    a split chunk, and their estimated tokens (as ``Chunk.tokens`` estimates them)."""
    tokens = f"ceil((char_length(c.header) + 1 + char_length(c.text))::numeric / {CHARS_PER_TOKEN})"
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "WITH per_node AS (SELECT node_id, count(*) AS parts FROM chunks "
                "WHERE snapshot_id = :s GROUP BY node_id) "
                "SELECT CASE WHEN n.kind = 'file' THEN 'module' ELSE n.kind END AS kind, "
                "count(*) AS total, "
                f"sum({tokens}) AS tokens, "
                # A part of a split chunk: its node has several chunks, or it is the one part
                # of a function, method, or test over the token limit (classes are never split).
                "sum(CASE WHEN p.parts > 1 OR (n.kind IN ('function', 'method', 'test') "
                f"AND {tokens} > {MAX_CHUNK_TOKENS}) THEN 1 ELSE 0 END) AS split_parts "
                "FROM chunks c JOIN nodes n ON n.id = c.node_id "
                "JOIN per_node p ON p.node_id = c.node_id "
                "WHERE c.snapshot_id = :s GROUP BY 1"
            ),
            {"s": snapshot_id},
        ).all()
    return {
        "count": sum(int(row.total) for row in rows),
        "by_kind": {row.kind: int(row.total) for row in sorted(rows, key=lambda row: row.kind)},
        "split_parts": sum(int(row.split_parts) for row in rows),
        "tokens": sum(int(row.tokens) for row in rows),
    }
