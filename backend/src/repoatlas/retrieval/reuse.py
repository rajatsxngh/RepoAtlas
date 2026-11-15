"""Chunks of an incremental build (PRF-2, PRF-3, decision 0024): copy what did not change.

A file's chunks depend on its text and on its own nodes, nothing else. So a file with the same
path and blob as in the previous snapshot of the repository (same config fingerprint, hence
the same parser and chunker) has the same chunks, and they are copied row by row instead of
being cut again; only their node IDs change, found by symbol key.

The copied and the newly cut chunks are inserted together, in path order, so chunk IDs rise in
the same order as in a full build. Search breaks ties by chunk ID, so this keeps an incremental
snapshot's results identical to a full rebuild's.

Which files can be copied, and which node each copied chunk belongs to, is worked out here from
four plain reads (each by one snapshot ID), not in one large join: the copy then joins the
previous snapshot's chunks to a list of node IDs, a plan that does not depend on table
statistics, which are stale right after a snapshot's rows are loaded.

``chunk_stats`` counts a snapshot's chunks from its rows, so a build that copied chunks
reports the same numbers as one that cut them all.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, text

from repoatlas.retrieval.chunker import CHARS_PER_TOKEN, MAX_CHUNK_TOKENS, Chunk
from repoatlas.retrieval.keyword import chunk_tsvector

INSERT_BATCH = 500
_COLUMNS = "blob_oid, path, start_line, end_line, chunk_hash, header, text, tsv"


@dataclass(frozen=True)
class ChunkReuse:
    """What a snapshot takes from its previous one: the files whose chunks are copied, and for
    every node those chunks belong to, its ID in the previous snapshot and in the new one."""

    paths: frozenset[str] = frozenset()
    node_ids: dict[int, int] = field(default_factory=dict)  # previous node ID → new node ID


def _indexed_files(connection: Connection, snapshot_id: int) -> dict[str, str]:
    rows = connection.execute(
        text("SELECT path, blob_oid FROM files WHERE snapshot_id = :s AND skip_reason IS NULL"),
        {"s": snapshot_id},
    )
    return {row.path: row.blob_oid for row in rows}


def plan_chunk_reuse(snapshot_id: int, previous_id: int, *, engine: Engine) -> ChunkReuse:
    """Indexed files of ``snapshot_id`` whose chunks can be copied from ``previous_id``: the
    same path and blob there, and every chunk's node found again by its symbol key. (A chunk
    whose node is missing would mean the file's nodes differ; the file is cut again instead.)
    A file without chunks there (an empty file) is copied too: it has none here either."""
    with engine.connect() as connection:
        new_files = _indexed_files(connection, snapshot_id)
        old_files = _indexed_files(connection, previous_id)
        same = {path for path, oid in new_files.items() if old_files.get(path) == oid}
        if not same:
            return ChunkReuse()
        new_nodes = {
            row.symbol_key: row.id
            for row in connection.execute(
                text("SELECT symbol_key, id FROM nodes WHERE snapshot_id = :s"),
                {"s": snapshot_id},
            )
        }
        chunk_nodes = connection.execute(
            text(
                "SELECT DISTINCT c.path, c.node_id, n.symbol_key FROM chunks c "
                "JOIN nodes n ON n.id = c.node_id WHERE c.snapshot_id = :s"
            ),
            {"s": previous_id},
        ).all()
    node_ids: dict[int, int] = {}
    broken: set[str] = set()
    for row in chunk_nodes:
        if row.path not in same:
            continue
        new_id = new_nodes.get(row.symbol_key)
        if new_id is None:
            broken.add(row.path)
        else:
            node_ids[row.node_id] = new_id
    paths = frozenset(same - broken)
    if broken:
        kept = {row.node_id for row in chunk_nodes if row.path in paths}
        node_ids = {old: new for old, new in node_ids.items() if old in kept}
    return ChunkReuse(paths=paths, node_ids=node_ids)


def save_chunks_reusing(
    snapshot_id: int,
    previous_id: int,
    reuse: ChunkReuse,
    chunks: Sequence[Chunk],
    *,
    engine: Engine,
    keyword_document: Callable[[Chunk], str] = chunk_tsvector,
) -> int:
    """Replace the snapshot's chunks with the previous snapshot's chunks of ``reuse`` plus
    ``chunks`` (the other files', newly cut), in one transaction. Returns how many were
    copied."""
    old_ids = sorted(reuse.node_ids)
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
                "SELECT m.new_id AS node_id, c.id AS seq, c.blob_oid, c.path, c.start_line, "
                "c.end_line, c.chunk_hash, c.header, c.text, c.tsv FROM chunks c "
                "JOIN unnest(CAST(:old AS bigint[]), CAST(:mapped AS bigint[])) "
                "AS m(old_id, new_id) ON m.old_id = c.node_id "
                "WHERE c.snapshot_id = :prev "
                "UNION ALL "
                f"SELECT node_id, seq, {_COLUMNS} FROM chunks_incoming"
                ") merged ORDER BY merged.path, merged.seq"
            ),
            {
                "new": snapshot_id,
                "prev": previous_id,
                "old": old_ids,
                "mapped": [reuse.node_ids[old] for old in old_ids],
            },
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
