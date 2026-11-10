"""Saving a snapshot's chunks, and reading them back (RET-1, RET-2).

``save_chunks`` replaces a snapshot's chunks in one transaction, so running it again (a
retried build) gives the same rows. ``tsv`` is the ``tsvector`` literal that
``keyword_document`` writes (by default the keyword index's split identifiers).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import Engine, text

from repoatlas.db.engine import get_engine
from repoatlas.retrieval.chunker import Chunk
from repoatlas.retrieval.keyword import chunk_tsvector

INSERT_BATCH = 500


def save_chunks(
    snapshot_id: int,
    chunks: Sequence[Chunk],
    *,
    engine: Engine | None = None,
    keyword_document: Callable[[Chunk], str] = chunk_tsvector,
) -> int:
    """Replace the snapshot's chunks with ``chunks``; returns how many were saved."""
    engine = engine or get_engine()
    insert = text(
        "INSERT INTO chunks (snapshot_id, node_id, blob_oid, path, start_line, end_line, "
        "chunk_hash, header, text, tsv) VALUES (:snapshot_id, :node_id, :blob_oid, :path, "
        ":start_line, :end_line, :chunk_hash, :header, :text, CAST(:doc AS tsvector))"
    )
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM chunks WHERE snapshot_id = :s"), {"s": snapshot_id})
        for offset in range(0, len(chunks), INSERT_BATCH):
            connection.execute(
                insert,
                [
                    {
                        "snapshot_id": snapshot_id,
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
                    for chunk in chunks[offset : offset + INSERT_BATCH]
                ],
            )
    return len(chunks)
