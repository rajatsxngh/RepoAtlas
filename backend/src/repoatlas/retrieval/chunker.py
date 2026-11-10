"""Symbol-level chunks for search (RET-1, RET-2).

Each indexed file of a snapshot becomes:

- one chunk per ``function``, ``method``, and ``test`` node, covering its span;
- one chunk per ``class`` node with the class's own lines (signature, docstring, attributes)
  and its members' signatures, with member bodies left out (an ``...`` line marks each);
- one module chunk with the file's top-level code outside every symbol, where each import
  statement is collapsed onto one line.

A chunk longer than ``MAX_CHUNK_TOKENS`` (estimated as characters / 4) is split at statement
boundaries, with ``OVERLAP_LINES`` lines repeated between consecutive parts; every part keeps
the node it belongs to. Every chunk carries its evidence reference (blob, path, lines) and a
``header`` (path, qualified name, signature) that is prepended for embedding and keyword search
but is not part of the code lines. Headers use only the chunk's own node and file.

``chunk_hash`` is the sha256 of header and text, so a chunk that did not change between
snapshots keeps its hash (and its cached embeddings). File text is read from the store's
``blobs`` table, never from a checkout.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from sqlalchemy import Engine, text

from repoatlas.db.engine import get_engine
from repoatlas.graph.model import CodeGraph
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.store.blobs import split_lines

CHUNKER_VERSION = 1
MAX_CHUNK_TOKENS = 1_200
CHARS_PER_TOKEN = 4
OVERLAP_LINES = 10
MAX_SIGNATURE_LINES = 8  # lines of a member kept in a class chunk, from its first line
ELIDED = "..."

ChunkKind = Literal["function", "method", "test", "class", "module"]
SPAN_KINDS: frozenset[str] = frozenset({"function", "method", "test", "class"})
_SYMBOL_CHUNK_KINDS: dict[str, ChunkKind] = {
    "function": "function",
    "method": "method",
    "test": "test",
}


def estimate_tokens(value: str) -> int:
    """Characters / 4, rounded up: the budget estimate used everywhere in retrieval."""
    return -(-len(value) // CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class ChunkNode:
    """What the chunker needs to know about one node of a file."""

    id: int
    key: str
    kind: str
    qualified_name: str
    start_line: int
    end_line: int
    name_line: int
    signature: str | None = None
    parent_key: str | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    node_id: int
    node_key: str
    kind: ChunkKind
    blob_oid: str
    path: str
    start_line: int
    end_line: int
    header: str
    text: str
    part: int = 0  # 0 for a whole chunk; 1, 2, ... for the parts of a split one
    chunk_hash: str = field(init=False)

    def __post_init__(self) -> None:
        digest = hashlib.sha256(self.document.encode("utf-8")).hexdigest()
        object.__setattr__(self, "chunk_hash", digest)

    @property
    def document(self) -> str:
        """Header and text: what is embedded and hashed."""
        return f"{self.header}\n{self.text}"

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.document)

    @property
    def ref(self) -> EvidenceRef:
        return EvidenceRef(
            blob_oid=self.blob_oid,
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
            symbol_key=None if self.kind == "module" else self.node_key,
        )


def make_header(path: str, qualified_name: str | None, signature: str | None) -> str:
    """``path``, then the qualified name and the signature when there are any."""
    parts = [path]
    if qualified_name:
        parts.append(qualified_name)
    if signature:
        parts.append(" ".join(signature.split()))
    return "\n".join(parts)


def chunk_file(
    *,
    path: str,
    blob_oid: str,
    source: str,
    file_node: ChunkNode,
    nodes: Sequence[ChunkNode],
    max_tokens: int = MAX_CHUNK_TOKENS,
    overlap: int = OVERLAP_LINES,
) -> list[Chunk]:
    """The chunks of one file: ``nodes`` are the file's symbols (other kinds are ignored)."""
    lines = split_lines(source)
    if not lines:
        return []
    symbols = sorted(
        (node for node in nodes if node.kind in SPAN_KINDS),
        key=lambda node: (node.start_line, -node.end_line, node.key),
    )
    chunks: list[Chunk] = []
    for node in symbols:
        start, end = max(node.start_line, 1), min(node.end_line, len(lines))
        if start > end:
            continue
        header = make_header(path, node.qualified_name, node.signature)
        if node.kind == "class":
            members = [other for other in symbols if other.parent_key == node.key]
            body = _class_text(lines, node, members)
            chunks.append(
                Chunk(node.id, node.key, "class", blob_oid, path, start, end, header, body)
            )
            continue
        kind = _SYMBOL_CHUNK_KINDS[node.kind]
        whole = Chunk(
            node.id, node.key, kind, blob_oid, path, start, end, header, _join(lines, start, end)
        )
        chunks.extend(_split(lines, whole, max_tokens=max_tokens, overlap=overlap))
    chunks.extend(
        _module_chunks(
            lines, symbols, path, blob_oid, file_node, max_tokens=max_tokens, overlap=overlap
        )
    )
    return chunks


def _join(lines: Sequence[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


# --- class chunks -------------------------------------------------------------------------


def _class_text(lines: Sequence[str], node: ChunkNode, members: Sequence[ChunkNode]) -> str:
    """The class's own lines, with each member reduced to its signature lines."""
    out: list[str] = []
    line = node.start_line
    last = min(node.end_line, len(lines))
    for member in sorted(members, key=lambda m: m.start_line):
        if member.start_line < line or member.start_line > last:
            continue  # overlapping an earlier member
        out.extend(lines[line - 1 : member.start_line - 1])
        member_end = min(member.end_line, last)
        kept_end = _signature_end(lines, member, member_end)
        out.extend(lines[member.start_line - 1 : kept_end])
        if kept_end < member_end:
            closing = lines[member_end - 1]
            closes = member_end > kept_end + 1 and closing.strip() in _CLOSERS
            out.append(_indent_of(lines, kept_end + 1, member_end - closes) + ELIDED)
            if closes:
                out.append(closing)
        line = member_end + 1
    out.extend(lines[line - 1 : last])
    return "\n".join(out)


_CLOSERS = frozenset({"}", "};", "},", "})", "});"})


def _signature_end(lines: Sequence[str], member: ChunkNode, member_end: int) -> int:
    """The last line of a member's signature: from its name line to the first line that opens
    the body (ends with ``:``, ``{``, or ``=>``), at most ``MAX_SIGNATURE_LINES`` lines."""
    limit = min(member_end, member.start_line + MAX_SIGNATURE_LINES - 1)
    for number in range(max(member.name_line, member.start_line), limit + 1):
        stripped = lines[number - 1].split("#", 1)[0].rstrip()
        if stripped.endswith((":", "{", "=>")):
            return number
    return max(min(member.name_line, limit), member.start_line)


def _indent_of(lines: Sequence[str], start: int, end: int) -> str:
    for number in range(start, end + 1):
        line = lines[number - 1]
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return ""


# --- module chunks ------------------------------------------------------------------------

Line = tuple[int, int, str]  # first and last source line, and the (possibly collapsed) text
_IMPORT_STARTS = ("import ", "from ", "export * from ", "export {")


def _module_chunks(
    lines: Sequence[str],
    symbols: Sequence[ChunkNode],
    path: str,
    blob_oid: str,
    file_node: ChunkNode,
    *,
    max_tokens: int,
    overlap: int,
) -> list[Chunk]:
    """The file's top-level code outside every symbol, as one chunk or as parts."""
    covered = bytearray(len(lines) + 2)
    for node in symbols:
        for number in range(max(node.start_line, 1), min(node.end_line, len(lines)) + 1):
            covered[number] = 1
    kept: list[int] = []
    for number in range(1, len(lines) + 1):
        blank = not lines[number - 1].strip()
        if not covered[number] and not (blank and kept and not lines[kept[-1] - 1].strip()):
            kept.append(number)  # runs of blank lines (where symbols were) count as one
    while kept and not lines[kept[0] - 1].strip():
        kept.pop(0)
    while kept and not lines[kept[-1] - 1].strip():
        kept.pop()
    if not kept:
        return []
    collapsed = _collapse_imports([(n, lines[n - 1]) for n in kept])
    header = make_header(path, None, None)
    budget = max_tokens * CHARS_PER_TOKEN - len(header) - 1
    if sum(len(line) + 1 for _, _, line in collapsed) <= budget:
        groups = [collapsed]
    else:
        groups = _pack(collapsed, budget, overlap)
    return [
        Chunk(
            file_node.id,
            file_node.key,
            "module",
            blob_oid,
            path,
            group[0][0],
            group[-1][1],
            header,
            "\n".join(line for _, _, line in group),
            0 if len(groups) == 1 else part,
        )
        for part, group in enumerate(groups, start=1)
    ]


def _collapse_imports(lines: Iterable[tuple[int, str]]) -> list[Line]:
    """Join each multi-line import statement onto one line and drop blank lines between
    imports, so a long import block reads as one line per module."""
    out: list[Line] = []
    pending: list[str] = []
    pending_start = pending_end = 0
    depth = 0
    for number, line in lines:
        stripped = line.strip()
        if pending:
            pending.append(stripped.rstrip("\\").rstrip())
            pending_end = number
            depth += _bracket_balance(stripped)
            if depth <= 0 and not stripped.endswith(("\\", ",")):
                out.append((pending_start, number, " ".join(p for p in pending if p)))
                pending = []
            continue
        if stripped.startswith(_IMPORT_STARTS) and line == line.lstrip():
            depth = _bracket_balance(stripped)
            if depth > 0 or stripped.endswith("\\"):
                pending = [stripped.rstrip("\\").rstrip()]
                pending_start = pending_end = number
                continue
            out.append((number, number, stripped))
            continue
        if not stripped and out and out[-1][2].startswith(_IMPORT_STARTS):
            continue  # a blank line inside an import block
        out.append((number, number, line))
    if pending:
        out.append((pending_start, pending_end, " ".join(p for p in pending if p)))
    return out


def _bracket_balance(value: str) -> int:
    return sum(value.count(c) for c in "([{") - sum(value.count(c) for c in ")]}")


def _pack(items: Sequence[Line], budget: int, overlap: int) -> list[list[Line]]:
    """Consecutive groups of ``items`` within ``budget`` characters, overlapping by
    ``overlap`` items (each group holds at least one item, and every group starts later)."""
    groups: list[list[Line]] = []
    first = 0
    while first < len(items):
        size, last = 0, first
        while last < len(items) and size + len(items[last][2]) + 1 <= budget:
            size += len(items[last][2]) + 1
            last += 1
        last = max(last, first + 1)
        groups.append(list(items[first:last]))
        if last >= len(items):
            break
        first = max(last - overlap, first + 1)
    return groups


# --- splitting long chunks ----------------------------------------------------------------


def _split(lines: Sequence[str], whole: Chunk, *, max_tokens: int, overlap: int) -> list[Chunk]:
    """``whole``, or its parts cut at statement boundaries when it is over ``max_tokens``."""
    if whole.tokens <= max_tokens:
        return [whole]
    budget = max_tokens * CHARS_PER_TOKEN - len(whole.header) - 1
    start, end = whole.start_line, whole.end_line
    boundaries = _statement_starts(lines, start, end)
    parts: list[Chunk] = []
    first = start
    while True:
        size = 0
        last_fit = first - 1
        for number in range(first, end + 1):
            size += len(lines[number - 1]) + 1
            if size > budget:
                break
            last_fit = number
        if last_fit >= end:
            cut = end + 1
        else:
            # Cut before the latest statement that starts after the overlap and still fits.
            cut = next(
                (b for b in reversed(boundaries) if first + overlap < b <= last_fit + 1),
                max(last_fit + 1, first + 1),  # no boundary fits: cut where the budget ends
            )
        parts.append(
            replace(
                whole,
                start_line=first,
                end_line=cut - 1,
                text=_join(lines, first, cut - 1),
                part=len(parts) + 1,
            )
        )
        if cut > end:
            return parts
        following = cut - overlap
        first = following if following > first else cut


def _statement_starts(lines: Sequence[str], start: int, end: int) -> list[int]:
    """Lines inside ``start``..``end`` that begin a statement at the body's indentation (or
    less): not blank, not a comment, and not continuing the previous line."""
    body_indent: int | None = None
    for number in range(start + 1, end + 1):
        line = lines[number - 1]
        if line.strip():
            body_indent = len(line) - len(line.lstrip())
            break
    if body_indent is None:
        return []
    starts: list[int] = []
    previous = lines[start - 1].rstrip()
    depth = _bracket_balance(previous)
    for number in range(start + 1, end + 1):
        line = lines[number - 1]
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        continues = depth > 0 or previous.endswith(("\\", ",", "(", "[", "{", "+", "&&", "||"))
        if (
            indent <= body_indent
            and not continues
            and not stripped.startswith(("#", "//", "*", ")", "]", "}", "."))
        ):
            starts.append(number)
        depth = max(depth + _bracket_balance(stripped), 0)
        previous = line.rstrip()
    return starts


# --- whole snapshots ----------------------------------------------------------------------


def chunk_graph(graph: CodeGraph, read_text: Callable[[str], str | None]) -> list[Chunk]:
    """Chunk an in-memory graph (for tests and tools): ``read_text(path)`` gives a file's text,
    or ``None`` for files without text. Node IDs are positions in key order, so they differ
    from store row IDs; everything else equals what ``chunk_snapshot`` gives."""
    ids = {key: position for position, key in enumerate(graph.nodes, start=1)}
    by_path: dict[str, list[ChunkNode]] = {}
    for node in graph.nodes.values():
        if node.kind.value in SPAN_KINDS and node.span is not None:
            signature = node.meta.get("signature")
            by_path.setdefault(node.path, []).append(
                ChunkNode(
                    id=ids[node.key],
                    key=node.key,
                    kind=node.kind.value,
                    qualified_name=node.qualified_name,
                    start_line=node.span.start_line,
                    end_line=node.span.end_line,
                    name_line=(node.name_range or node.span).start_line,
                    signature=signature if isinstance(signature, str) else None,
                    parent_key=node.parent_key,
                )
            )
    chunks: list[Chunk] = []
    for node in sorted(graph.nodes.values(), key=lambda n: n.path):
        if node.kind.value != "file" or node.meta.get("skip_reason"):
            continue
        source = read_text(node.path)
        oid = node.meta.get("blob_oid")
        if source is None or not isinstance(oid, str):
            continue
        file_node = ChunkNode(ids[node.key], node.key, "file", node.key, 1, 1, 1)
        chunks.extend(
            chunk_file(
                path=node.path,
                blob_oid=oid,
                source=source,
                file_node=file_node,
                nodes=by_path.get(node.path, []),
            )
        )
    return chunks


def chunk_snapshot(snapshot_id: int, *, engine: Engine | None = None) -> list[Chunk]:
    """Chunk every indexed file of a stored snapshot, reading file text from ``blobs``.

    The snapshot need not be ``ready``: the build chunks it right after storing it. Chunks
    come in path order, then in the order ``chunk_file`` makes them."""
    engine = engine or get_engine()
    with engine.connect() as connection:
        files = connection.execute(
            text(
                "SELECT f.id, f.path, f.blob_oid, b.text FROM files f "
                "JOIN blobs b ON b.oid = f.blob_oid "
                "WHERE f.snapshot_id = :s AND f.skip_reason IS NULL ORDER BY f.path"
            ),
            {"s": snapshot_id},
        ).all()
        rows = connection.execute(
            text(
                "SELECT n.id, n.symbol_key, n.kind, n.qualified_name, n.file_id, "
                "n.span_start_line, n.span_end_line, n.name_start_line, "
                "n.meta ->> 'signature' AS signature, p.symbol_key AS parent_key "
                "FROM nodes n LEFT JOIN nodes p ON p.id = n.parent_id "
                "WHERE n.snapshot_id = :s AND n.file_id IS NOT NULL "
                "AND n.kind IN ('file', 'function', 'method', 'test', 'class')"
            ),
            {"s": snapshot_id},
        ).all()
    by_file: dict[int, list[ChunkNode]] = {}
    file_nodes: dict[int, ChunkNode] = {}
    for row in rows:
        if row.kind == "file":
            file_nodes[row.file_id] = ChunkNode(
                row.id, row.symbol_key, "file", row.symbol_key, 1, 1, 1
            )
        elif row.span_start_line is not None and row.span_end_line is not None:
            by_file.setdefault(row.file_id, []).append(
                ChunkNode(
                    id=row.id,
                    key=row.symbol_key,
                    kind=row.kind,
                    qualified_name=row.qualified_name,
                    start_line=row.span_start_line,
                    end_line=row.span_end_line,
                    name_line=row.name_start_line or row.span_start_line,
                    signature=row.signature,
                    parent_key=row.parent_key,
                )
            )
    chunks: list[Chunk] = []
    for file in files:
        file_node = file_nodes.get(file.id)
        if file_node is not None:
            chunks.extend(
                chunk_file(
                    path=file.path,
                    blob_oid=file.blob_oid,
                    source=file.text,
                    file_node=file_node,
                    nodes=by_file.get(file.id, []),
                )
            )
    return chunks
