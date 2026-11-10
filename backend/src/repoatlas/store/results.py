"""What ``GraphStore`` reads return. Nodes and edges are the graph's own models (keys, never row
IDs), so a result can be compared with, or merged into, an in-memory ``CodeGraph``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from repoatlas.graph.model import Edge, EdgeKind, Node
from repoatlas.store.cache import Direction, Level
from repoatlas.store.models import SnapshotStatus

MatchKind = Literal["exact", "prefix", "fuzzy"]


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RepoInfo(_Result):
    host: str
    owner: str
    name: str
    default_branch: str


class SnapshotInfo(_Result):
    id: int
    repo: RepoInfo
    commit_sha: str
    config_fingerprint: str
    status: SnapshotStatus
    stats: dict[str, JsonValue]
    embedding_model: str | None
    embedding_dim: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class SymbolMatch(_Result):
    """A search hit: ``exact`` (key, name, or qualified name, ignoring case), ``prefix``, or
    ``fuzzy`` (trigram similarity); ``score`` is the best trigram similarity (0 to 1)."""

    node: Node
    match: MatchKind
    score: float


class Hop(_Result):
    node: Node
    depth: int


class Neighborhood(_Result):
    """Nodes within ``depth`` hops of ``root`` (the root excluded), by depth then key, and the
    edges the search followed, in canonical order. ``truncated``: the node cap was hit."""

    root: Node
    direction: Direction
    kinds: list[EdgeKind]
    depth: int
    nodes: list[Hop]
    edges: list[Edge]
    truncated: bool = False


class GraphPath(_Result):
    """``edges[i]`` links ``nodes[i]`` and ``nodes[i + 1]``. ``directed`` is false when no path
    follows the edge directions; then some edges point backwards along the path."""

    nodes: list[Node]
    edges: list[Edge]
    directed: bool
    kinds: list[EdgeKind]


class AggregatedEdge(_Result):
    """Edges of one kind between two children: how many, and their confidence range."""

    src: str
    dst: str
    kind: EdgeKind
    count: int
    min_confidence: float
    max_confidence: float


class Subgraph(_Result):
    """The children of ``parent`` at ``level`` (key order) and the edges between them."""

    parent: Node
    level: Level
    nodes: list[Node]
    edges: list[AggregatedEdge]


class EdgeLinks(_Result):
    """The edges behind one aggregated edge: edges of ``kind`` from ``src`` or below it to
    ``dst`` or below it, strongest first; ``total`` counts all of them."""

    src: str
    dst: str
    kind: EdgeKind
    total: int
    links: list[Edge]
    nodes: list[Node]


class Ranked(_Result):
    """A node with how often other files use it, and from how many files."""

    node: Node
    uses: int
    files: int


class Overview(_Result):
    """Where to start reading a snapshot.

    ``entry_points``: routes, else public symbols · ``core_files`` / ``core_symbols``: the most
    used from other files (imports and calls, guesses left out) · ``tested_files``: source files
    with at least one ``tested_by`` edge, out of ``source_files`` (test files excluded).
    """

    counts: dict[str, int]
    entry_points: list[Node]
    core_files: list[Ranked]
    core_symbols: list[Ranked]
    tested_files: int
    source_files: int


class CodeSlice(_Result):
    """Lines ``start`` to ``end`` (1-based, inclusive, after clamping) of a file's stored text,
    joined by ``\\n``; ``line_count`` is the number of lines in the whole file."""

    path: str
    blob_oid: str
    start: int
    end: int
    text: str
    line_count: int
