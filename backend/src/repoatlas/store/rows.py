"""Graph models ↔ store rows, in both directions, kept side by side so they always agree.

Writers produce plain tuples for ``COPY`` (JSON as canonical text, which the server parses into
``jsonb``); readers turn SQL rows back into the frozen graph models. Columns are listed once
here and reused by the loader and the read API.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from repoatlas.canonical import canonical_json
from repoatlas.graph.model import (
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    Owner,
    Provenance,
    UnresolvedCall,
)
from repoatlas.ingest.inventory import FileEntry
from repoatlas.parsing.models import Range

RANGE_PARTS = ("start_line", "start_col", "end_line", "end_col")
RangeValues = tuple[int | None, int | None, int | None, int | None]

# The callee's last identifier: ``append`` in ``self.items.append``; none for ``handlers[k]``.
_TRAILING_NAME = re.compile(r"(?:[^\W\d]|\$)(?:\w|\$)*$")


def range_columns(prefix: str) -> tuple[str, ...]:
    return tuple(f"{prefix}_{part}" for part in RANGE_PARTS)


def range_values(value: Range | None) -> RangeValues:
    if value is None:
        return (None, None, None, None)
    return (value.start_line, value.start_col, value.end_line, value.end_col)


def range_from(values: Sequence[Any]) -> Range | None:
    start_line, start_col, end_line, end_col = values
    if start_line is None:
        return None
    return Range(start_line=start_line, start_col=start_col, end_line=end_line, end_col=end_col)


def callee_name(callee_text: str) -> str | None:
    match = _TRAILING_NAME.search(callee_text)
    return match.group(0) if match else None


# --- writing (COPY rows) ---------------------------------------------------------------------

FILE_COLUMNS = (
    "id",
    "snapshot_id",
    "path",
    "blob_oid",
    "mode",
    "size_bytes",
    "language",
    "loc",
    "is_test",
    "skip_reason",
)
NODE_COLUMNS = (
    "id",
    "snapshot_id",
    "symbol_key",
    "kind",
    "name",
    "qualified_name",
    "path",
    "file_id",
    *range_columns("name"),
    *range_columns("span"),
    "parent_id",
    "exported",
    "meta",
)
EDGE_COLUMNS = (
    "snapshot_id",
    "src_id",
    "dst_id",
    "kind",
    "provenance",
    "confidence",
    *range_columns("site"),
    "meta",
)
UNRESOLVED_COLUMNS = (
    "snapshot_id",
    "caller_id",
    "callee_text",
    "callee_name",
    "receiver_text",
    *range_columns("site"),
    "candidates",
    "reason",
)
OWNER_COLUMNS = (
    "snapshot_id",
    "file_id",
    "author_hash",
    "author_name",
    "commits",
    "last_commit_at",
    "share",
)
BLOB_COLUMNS = ("oid", "size_bytes", "line_count", "text", "lossy")


def file_row(entry: FileEntry, *, file_id: int, snapshot_id: int) -> tuple[object, ...]:
    return (
        file_id,
        snapshot_id,
        entry.path,
        entry.blob_oid,
        entry.mode,
        entry.size,
        entry.language,
        entry.loc,
        entry.is_test,
        entry.skip_reason,
    )


def node_row(
    node: Node, *, node_id: int, snapshot_id: int, file_id: int | None, parent_id: int | None
) -> tuple[object, ...]:
    return (
        node_id,
        snapshot_id,
        node.key,
        node.kind.value,
        node.name,
        node.qualified_name,
        node.path,
        file_id,
        *range_values(node.name_range),
        *range_values(node.span),
        parent_id,
        node.exported,
        canonical_json(node.meta),
    )


def edge_row(edge: Edge, *, snapshot_id: int, src_id: int, dst_id: int) -> tuple[object, ...]:
    return (
        snapshot_id,
        src_id,
        dst_id,
        edge.kind.value,
        edge.provenance.value,
        edge.confidence,
        *range_values(edge.site),
        canonical_json(edge.meta),
    )


def unresolved_row(call: UnresolvedCall, *, snapshot_id: int, caller_id: int) -> tuple[object, ...]:
    return (
        snapshot_id,
        caller_id,
        call.callee_text,
        callee_name(call.callee_text),
        call.receiver_text,
        *range_values(call.site),
        list(call.candidates),
        call.reason,
    )


def owner_row(owner: Owner, *, snapshot_id: int, file_id: int) -> tuple[object, ...]:
    return (
        snapshot_id,
        file_id,
        owner.author_hash,
        owner.author_name,
        owner.commits,
        owner.last_commit_at,
        owner.share,
    )


# --- reading (SQL rows → models) -------------------------------------------------------------

# Select lists for nodes ``n`` (with their parent ``p``), edges ``e`` (with endpoints ``s`` and
# ``d``), unresolved calls ``u`` (with their caller ``c``), and owners ``o``.
NODE_SELECT = ", ".join(
    [
        "n.id",
        "n.symbol_key",
        "n.kind",
        "n.name",
        "n.qualified_name",
        "n.path",
        *(f"n.{column}" for column in (*range_columns("name"), *range_columns("span"))),
        "n.exported",
        "n.meta",
        "p.symbol_key AS parent_key",
    ]
)
NODE_FROM = "nodes n LEFT JOIN nodes p ON p.id = n.parent_id"
EDGE_SELECT = ", ".join(
    [
        "e.id",
        "s.symbol_key AS src_key",
        "d.symbol_key AS dst_key",
        "e.kind",
        "e.provenance",
        "e.confidence",
        *(f"e.{column}" for column in range_columns("site")),
        "e.meta",
    ]
)
EDGE_FROM = "edges e JOIN nodes s ON s.id = e.src_id JOIN nodes d ON d.id = e.dst_id"
UNRESOLVED_SELECT = ", ".join(
    [
        "c.symbol_key AS caller_key",
        "u.callee_text",
        "u.receiver_text",
        *(f"u.{column}" for column in range_columns("site")),
        "u.candidates",
        "u.reason",
    ]
)
UNRESOLVED_FROM = "unresolved_calls u JOIN nodes c ON c.id = u.caller_id"
OWNER_SELECT = "o.author_name, o.author_hash, o.commits, o.last_commit_at, o.share"


def node_from_row(row: Any) -> Node:
    return Node(
        key=row.symbol_key,
        kind=NodeKind(row.kind),
        name=row.name,
        qualified_name=row.qualified_name,
        path=row.path,
        name_range=range_from(
            (row.name_start_line, row.name_start_col, row.name_end_line, row.name_end_col)
        ),
        span=range_from(
            (row.span_start_line, row.span_start_col, row.span_end_line, row.span_end_col)
        ),
        parent_key=row.parent_key,
        exported=row.exported,
        meta=row.meta,
    )


def edge_from_row(row: Any) -> Edge:
    return Edge(
        src=row.src_key,
        dst=row.dst_key,
        kind=EdgeKind(row.kind),
        provenance=Provenance(row.provenance),
        confidence=row.confidence,
        site=range_from(
            (row.site_start_line, row.site_start_col, row.site_end_line, row.site_end_col)
        ),
        meta=row.meta,
    )


def unresolved_from_row(row: Any) -> UnresolvedCall:
    site = range_from(
        (row.site_start_line, row.site_start_col, row.site_end_line, row.site_end_col)
    )
    assert site is not None  # the site columns are NOT NULL
    return UnresolvedCall(
        caller_key=row.caller_key,
        callee_text=row.callee_text,
        receiver_text=row.receiver_text,
        site=site,
        candidates=list(row.candidates),
        reason=row.reason,
    )


def owner_from_row(row: Any) -> Owner:
    last: datetime = row.last_commit_at
    return Owner(
        author_name=row.author_name,
        author_hash=row.author_hash,
        commits=row.commits,
        last_commit_at=last,
        share=row.share,
    )
