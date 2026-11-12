"""What the impact engine reads: a snapshot's adjacency plus node, edge, and file details.

``repoatlas.impact.store_data.StoreImpactData`` reads a stored snapshot through ``GraphStore``;
``MemoryImpactData`` serves an in-memory ``CodeGraph`` with its file texts (tests and tools).
Both answer the same way.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from repoatlas.graph.keys import file_key
from repoatlas.graph.model import CodeGraph, Edge, Node, NodeKind, UnresolvedCall
from repoatlas.ingest.source import blob_oid
from repoatlas.store.blobs import split_lines
from repoatlas.store.cache import SnapshotGraph
from repoatlas.store.errors import InvalidQuery, NodeNotFound, PathNotInSnapshot
from repoatlas.store.graphstore import LOCATABLE_KINDS


@dataclass(frozen=True, slots=True)
class TextSite:
    """One line of a file that holds a string literal equal to a name."""

    path: str
    blob_oid: str
    line: int
    literal: str


def literal_pattern(name: str) -> re.Pattern[str]:
    """A quoted string whose whole content is ``name``: ``"name"``, ``'name'``, or `` `name` ``."""
    escaped = re.escape(name)
    return re.compile(rf"""(?P<q>["'`]){escaped}(?P=q)""")


def literal_sites_in(path: str, oid: str, text: str, name: str) -> list[TextSite]:
    pattern = literal_pattern(name)
    sites = []
    for number, line in enumerate(split_lines(text), start=1):
        match = pattern.search(line)
        if match is not None:
            sites.append(TextSite(path=path, blob_oid=oid, line=number, literal=match.group(0)))
    return sites


class ImpactData(Protocol):
    def graph(self, snapshot_id: int) -> SnapshotGraph: ...

    def nodes(self, snapshot_id: int, keys: Sequence[str]) -> dict[str, Node]: ...

    def edges(self, snapshot_id: int, positions: Sequence[int]) -> dict[int, Edge]:
        """Edges by their position in ``graph(snapshot_id)``."""
        ...

    def blob_oids(self, snapshot_id: int, paths: Sequence[str]) -> dict[str, str]: ...

    def unresolved_calls(self, snapshot_id: int, name: str) -> list[UnresolvedCall]: ...

    def literal_sites(self, snapshot_id: int, name: str, limit: int) -> list[TextSite]: ...

    def locate(self, snapshot_id: int, path: str, line: int) -> Node:
        """The innermost class, function, method, or test containing the line, else the file;
        ``PathNotInSnapshot`` for unknown paths."""
        ...


class MemoryImpactData:
    """A ``CodeGraph`` and its files' texts, served as snapshot ``snapshot_id``."""

    def __init__(self, graph: CodeGraph, files: Mapping[str, str], *, snapshot_id: int = 1) -> None:
        self.code_graph = graph
        self.files = dict(files)
        self.snapshot_id = snapshot_id
        self.adjacency = SnapshotGraph.from_code_graph(graph, snapshot_id)
        # from_code_graph numbers edges from 1 in graph order; positions follow sorted order
        self._edge_by_id = dict(enumerate(graph.edges, start=1))

    def _check(self, snapshot_id: int) -> None:
        if snapshot_id != self.snapshot_id:
            raise NodeNotFound(f"No snapshot {snapshot_id} in memory.")

    def graph(self, snapshot_id: int) -> SnapshotGraph:
        self._check(snapshot_id)
        return self.adjacency

    def nodes(self, snapshot_id: int, keys: Sequence[str]) -> dict[str, Node]:
        self._check(snapshot_id)
        return {key: self.code_graph.nodes[key] for key in keys if key in self.code_graph.nodes}

    def edges(self, snapshot_id: int, positions: Sequence[int]) -> dict[int, Edge]:
        self._check(snapshot_id)
        ids = self.adjacency.edge_ids
        return {position: self._edge_by_id[ids[position]] for position in positions}

    def blob_oids(self, snapshot_id: int, paths: Sequence[str]) -> dict[str, str]:
        self._check(snapshot_id)
        return {
            path: blob_oid(self.files[path].encode("utf-8")) for path in paths if path in self.files
        }

    def unresolved_calls(self, snapshot_id: int, name: str) -> list[UnresolvedCall]:
        self._check(snapshot_id)
        return [
            call
            for call in self.code_graph.unresolved
            if call.callee_text == name or call.callee_text.rsplit(".", 1)[-1] == name
        ]

    def literal_sites(self, snapshot_id: int, name: str, limit: int) -> list[TextSite]:
        self._check(snapshot_id)
        found: list[TextSite] = []
        for path in sorted(self.files):
            text = self.files[path]
            if name in text:
                found.extend(literal_sites_in(path, blob_oid(text.encode("utf-8")), text, name))
        return found[:limit]

    def locate(self, snapshot_id: int, path: str, line: int) -> Node:
        self._check(snapshot_id)
        key = file_key(path)
        if key not in self.code_graph.nodes:
            raise PathNotInSnapshot(f"No file {path!r}.")
        if line < 1:
            raise InvalidQuery("Lines start at 1.")
        order = {"method": 0, "function": 1, "test": 2}
        candidates = [
            node
            for node in self.code_graph.nodes.values()
            if node.path == path
            and node.kind.value in LOCATABLE_KINDS
            and node.span is not None
            and node.span.start_line <= line <= node.span.end_line
        ]
        if not candidates:
            return self.code_graph.nodes[key]
        return min(
            candidates,
            key=lambda n: (
                -n.span.start_line,  # type: ignore[union-attr]
                -n.span.start_col,  # type: ignore[union-attr]
                n.span.end_line,  # type: ignore[union-attr]
                n.span.end_col,  # type: ignore[union-attr]
                order.get(n.kind.value, 3),
                n.key,
            ),
        )


def is_file(node: Node) -> bool:
    return node.kind == NodeKind.FILE
