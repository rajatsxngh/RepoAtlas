"""Saving a snapshot's chunks, and reading them back (RET-1, RET-2).

``save_chunks`` replaces a snapshot's chunks in one transaction, so running it again (a
retried build) gives the same rows. ``tsv`` is the ``tsvector`` literal that
``keyword_document`` writes (by default the keyword index's split identifiers). ``load_chunks``
reads saved chunks back with the node each belongs to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text

from repoatlas.db.engine import get_engine
from repoatlas.retrieval.chunker import Chunk, estimate_tokens
from repoatlas.retrieval.evidence import EvidenceRef
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


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """A saved chunk with the node it belongs to."""

    id: int
    node_id: int
    node_key: str
    node_name: str
    qualified_name: str
    node_kind: str
    blob_oid: str
    path: str
    start_line: int
    end_line: int
    header: str
    text: str

    @property
    def kind(self) -> str:
        """The chunk's kind: the node's kind, or ``module`` for a file's top-level code."""
        return "module" if self.node_kind == "file" else self.node_kind

    @property
    def tokens(self) -> int:
        return estimate_tokens(f"{self.header}\n{self.text}")

    @property
    def ref(self) -> EvidenceRef:
        return EvidenceRef(
            blob_oid=self.blob_oid,
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
            symbol_key=None if self.kind == "module" else self.node_key,
        )


_STORED_SELECT = (
    "SELECT c.id, c.node_id, n.symbol_key, n.name, n.qualified_name, n.kind, c.blob_oid, "
    "c.path, c.start_line, c.end_line, c.header, c.text FROM chunks c "
    "JOIN nodes n ON n.id = c.node_id "
    "WHERE c.snapshot_id = :s "
)


def _stored(row: Any) -> StoredChunk:
    return StoredChunk(
        id=row.id,
        node_id=row.node_id,
        node_key=row.symbol_key,
        node_name=row.name,
        qualified_name=row.qualified_name,
        node_kind=row.kind,
        blob_oid=row.blob_oid,
        path=row.path,
        start_line=row.start_line,
        end_line=row.end_line,
        header=row.header,
        text=row.text,
    )


def load_chunks(
    snapshot_id: int,
    *,
    ids: Iterable[int] = (),
    node_ids: Iterable[int] = (),
    engine: Engine | None = None,
) -> dict[int, StoredChunk]:
    """The snapshot's chunks with the given chunk IDs or belonging to the given nodes, by ID."""
    chunk_ids, nodes = sorted(set(ids)), sorted(set(node_ids))
    if not chunk_ids and not nodes:
        return {}
    engine = engine or get_engine()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                _STORED_SELECT + "AND (c.id = ANY(:ids) OR c.node_id = ANY(:nodes)) ORDER BY c.id"
            ),
            {"s": snapshot_id, "ids": chunk_ids, "nodes": nodes},
        ).all()
    return {row.id: _stored(row) for row in rows}
