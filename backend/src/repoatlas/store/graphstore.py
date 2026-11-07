"""Reads over stored snapshots (STO-4): lookups and search in SQL, traversals in memory.

Every method takes a snapshot ID and names nodes by symbol key; row IDs never leave the store.
Only ``ready`` snapshots can be read (``SnapshotNotReady`` otherwise), and since a ready
snapshot never changes, its adjacency is cached (``SnapshotGraphCache``) and reused by every
traversal. A traversal finds row IDs in memory, then fetches just those nodes and edges.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, cast

import psycopg
from sqlalchemy import Connection, Engine, text

from repoatlas.config import Settings
from repoatlas.db.engine import get_engine
from repoatlas.graph.model import (
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    Owner,
    UnresolvedCall,
    edge_sort_key,
    owner_sort_key,
    unresolved_sort_key,
)
from repoatlas.store import rows
from repoatlas.store.cache import (
    DIRECTIONS,
    Direction,
    EdgeRecord,
    NodeRecord,
    SnapshotGraph,
    SnapshotGraphCache,
)
from repoatlas.store.errors import (
    InvalidQuery,
    NodeNotFound,
    PathNotInSnapshot,
    SnapshotNotFound,
    SnapshotNotReady,
)
from repoatlas.store.results import (
    GraphPath,
    Hop,
    MatchKind,
    Neighborhood,
    RepoInfo,
    SnapshotInfo,
    SymbolMatch,
)

DEFAULT_PATH_KINDS = (EdgeKind.CALLS, EdgeKind.IMPORTS)
DEFAULT_MAX_NODES = 1_000  # nodes one traversal returns at most
MAX_DEPTH = 20
MAX_SEARCH_LIMIT = 100
_MATCHES: tuple[MatchKind, ...] = ("exact", "exact", "prefix", "fuzzy")


class GraphStore:
    """Read API for snapshots in Postgres. Give each process (or app) one store: it owns the
    adjacency cache, which is keyed by snapshot ID within one database."""

    def __init__(
        self,
        engine: Engine | None = None,
        cache: SnapshotGraphCache | None = None,
        *,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> None:
        self.engine = engine or get_engine()
        self.cache = cache or SnapshotGraphCache()
        self.max_nodes = max_nodes
        self._ready: set[int] = set()

    @classmethod
    def from_settings(cls, settings: Settings, engine: Engine | None = None) -> GraphStore:
        return cls(engine, SnapshotGraphCache.from_settings(settings))

    # --- snapshots ------------------------------------------------------------------------

    def snapshot(self, snapshot_id: int) -> SnapshotInfo:
        """A snapshot's identity, status, stats, and error, whatever its status."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT s.*, r.host, r.owner, r.name, r.default_branch FROM snapshots s "
                    "JOIN repos r ON r.id = s.repo_id WHERE s.id = :s"
                ),
                {"s": snapshot_id},
            ).first()
        if row is None:
            raise SnapshotNotFound(f"Snapshot {snapshot_id} does not exist.")
        return SnapshotInfo(
            id=row.id,
            repo=RepoInfo(
                host=row.host, owner=row.owner, name=row.name, default_branch=row.default_branch
            ),
            commit_sha=row.commit_sha,
            config_fingerprint=row.config_fingerprint,
            status=row.status,
            stats=row.stats,
            embedding_model=row.embedding_model,
            embedding_dim=row.embedding_dim,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def graph(self, snapshot_id: int) -> SnapshotGraph:
        """The snapshot's in-memory adjacency (loaded into the cache on first use)."""
        self._require_ready(snapshot_id)
        return self.cache.get(snapshot_id, lambda: self._load_adjacency(snapshot_id))

    # --- nodes ----------------------------------------------------------------------------

    def get_node(self, snapshot_id: int, key: str) -> Node | None:
        with self.engine.connect() as connection:
            self._require_ready(snapshot_id, connection)
            row = connection.execute(
                text(
                    f"SELECT {rows.NODE_SELECT} FROM {rows.NODE_FROM} "
                    "WHERE n.snapshot_id = :s AND n.symbol_key = :key"
                ),
                {"s": snapshot_id, "key": key},
            ).first()
        return None if row is None else rows.node_from_row(row)

    def find_symbols(
        self,
        snapshot_id: int,
        query: str,
        kinds: Iterable[NodeKind | str] | None = None,
        limit: int = 20,
    ) -> list[SymbolMatch]:
        """Nodes whose key, name, or qualified name matches ``query``: exact matches first (any
        case), then prefixes, then trigram matches; ties by similarity, then shorter names."""
        needle = query.strip()
        if not needle:
            raise InvalidQuery("The search query is empty.")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise InvalidQuery(f"The limit must be between 1 and {MAX_SEARCH_LIMIT}.")
        params: dict[str, Any] = {
            "s": snapshot_id,
            "q": needle,
            "prefix": _like_escape(needle) + "%",
            "limit": limit,
        }
        kind_filter = ""
        if kinds is not None:
            params["kinds"] = sorted({NodeKind(kind).value for kind in kinds})
            kind_filter = "AND n.kind = ANY(:kinds)"
        statement = text(
            f"SELECT {rows.NODE_SELECT}, "
            "CASE WHEN n.symbol_key = :q OR n.name = :q OR n.qualified_name = :q THEN 0 "
            "WHEN lower(n.name) = lower(:q) OR lower(n.qualified_name) = lower(:q) THEN 1 "
            "WHEN n.name ILIKE :prefix OR n.qualified_name ILIKE :prefix THEN 2 "
            "ELSE 3 END AS rank, "
            "greatest(similarity(n.name, :q), similarity(n.qualified_name, :q)) AS score "
            f"FROM {rows.NODE_FROM} WHERE n.snapshot_id = :s {kind_filter} "
            "AND (n.symbol_key = :q OR n.name ILIKE :prefix OR n.qualified_name ILIKE :prefix "
            "OR n.name % :q OR n.qualified_name % :q) "
            "ORDER BY rank, score DESC, length(n.qualified_name), n.symbol_key LIMIT :limit"
        )
        with self.engine.connect() as connection:
            self._require_ready(snapshot_id, connection)
            found = connection.execute(statement, params).all()
        return [
            SymbolMatch(
                node=rows.node_from_row(row),
                match=_MATCHES[row.rank],
                score=round(float(row.score), 4),
            )
            for row in found
        ]

    # --- traversals -----------------------------------------------------------------------

    def neighbors(
        self,
        snapshot_id: int,
        key: str,
        direction: Direction,
        kinds: Iterable[EdgeKind | str] | None = None,
        depth: int = 1,
    ) -> Neighborhood:
        """Nodes within ``depth`` hops of ``key`` along edges of ``kinds`` (default: all)."""
        if direction not in DIRECTIONS:
            raise InvalidQuery(f"The direction must be one of {', '.join(DIRECTIONS)}.")
        if not 1 <= depth <= MAX_DEPTH:
            raise InvalidQuery(f"The depth must be between 1 and {MAX_DEPTH}.")
        chosen = _edge_kinds(kinds)
        graph = self.graph(snapshot_id)
        start = _position(graph, key)
        found = graph.traverse(
            start, direction=direction, kinds=chosen, depth=depth, max_nodes=self.max_nodes
        )
        with self.engine.connect() as connection:
            nodes = _nodes_by_id(connection, [graph.node_ids[n] for n in found.depths])
            edges = _edges_by_id(connection, [graph.edge_ids[e] for e in found.edges])
        hops = sorted(
            (
                Hop(node=nodes[graph.node_ids[n]], depth=level)
                for n, level in found.depths.items()
                if n != start
            ),
            key=lambda hop: (hop.depth, hop.node.key),
        )
        return Neighborhood(
            root=nodes[graph.node_ids[start]],
            direction=direction,
            kinds=list(chosen),
            depth=depth,
            nodes=hops,
            edges=sorted(edges.values(), key=edge_sort_key),
            truncated=found.truncated,
        )

    def callers(self, snapshot_id: int, key: str, depth: int = 1) -> Neighborhood:
        """Who calls ``key``, directly and up to ``depth`` calls away."""
        return self.neighbors(snapshot_id, key, "in", [EdgeKind.CALLS], depth)

    def callees(self, snapshot_id: int, key: str, depth: int = 1) -> Neighborhood:
        """What ``key`` calls, directly and up to ``depth`` calls away."""
        return self.neighbors(snapshot_id, key, "out", [EdgeKind.CALLS], depth)

    def shortest_path(
        self,
        snapshot_id: int,
        src: str,
        dst: str,
        kinds: Iterable[EdgeKind | str] | None = None,
    ) -> GraphPath | None:
        """A shortest path from ``src`` to ``dst`` over ``kinds`` (default: calls and imports);
        an undirected one (``directed = False``) when no directed path exists; else ``None``."""
        chosen = _edge_kinds(kinds, default=DEFAULT_PATH_KINDS)
        graph = self.graph(snapshot_id)
        found = graph.shortest_path(_position(graph, src), _position(graph, dst), chosen)
        if found is None:
            return None
        node_ids = [graph.node_ids[n] for n in found.nodes]
        edge_ids = [graph.edge_ids[e] for e in found.edges]
        with self.engine.connect() as connection:
            nodes = _nodes_by_id(connection, node_ids)
            edges = _edges_by_id(connection, edge_ids)
        return GraphPath(
            nodes=[nodes[node_id] for node_id in node_ids],
            edges=[edges[edge_id] for edge_id in edge_ids],
            directed=found.directed,
            kinds=list(chosen),
        )

    # --- per-file and per-name data -------------------------------------------------------

    def owners(self, snapshot_id: int, path: str) -> list[Owner]:
        """Authors of ``path``, most commits first (empty for files without history)."""
        with self.engine.connect() as connection:
            self._require_ready(snapshot_id, connection)
            file_id = _file_id(connection, snapshot_id, path)
            owners = [
                rows.owner_from_row(row)
                for row in connection.execute(
                    text(
                        f"SELECT {rows.OWNER_SELECT} FROM owners o "
                        "WHERE o.snapshot_id = :s AND o.file_id = :f"
                    ),
                    {"s": snapshot_id, "f": file_id},
                )
            ]
        return sorted(owners, key=owner_sort_key)

    def unresolved_calls(self, snapshot_id: int, name: str) -> list[UnresolvedCall]:
        """Call sites without an edge whose callee is ``name``: the last identifier
        (``append``) or the whole callee text (``self.items.append``)."""
        with self.engine.connect() as connection:
            self._require_ready(snapshot_id, connection)
            found = [
                rows.unresolved_from_row(row)
                for row in connection.execute(
                    text(
                        f"SELECT {rows.UNRESOLVED_SELECT} FROM {rows.UNRESOLVED_FROM} "
                        "WHERE u.snapshot_id = :s AND (u.callee_name = :name "
                        "OR u.callee_text = :name)"
                    ),
                    {"s": snapshot_id, "name": name},
                )
            ]
        return sorted(found, key=unresolved_sort_key)

    # --- internals ------------------------------------------------------------------------

    def _require_ready(self, snapshot_id: int, connection: Connection | None = None) -> None:
        if snapshot_id in self._ready:
            return
        if connection is None:
            with self.engine.connect() as fresh:
                status = _status(fresh, snapshot_id)
        else:
            status = _status(connection, snapshot_id)
        if status is None:
            raise SnapshotNotFound(f"Snapshot {snapshot_id} does not exist.")
        if status != "ready":
            raise SnapshotNotReady(f"Snapshot {snapshot_id} is {status}.", status=status)
        self._ready.add(snapshot_id)

    def _load_adjacency(self, snapshot_id: int) -> SnapshotGraph:
        with self.engine.connect() as connection:
            raw = cast(psycopg.Connection[Any], connection.connection.driver_connection)
            with raw.cursor() as cursor:
                cursor.execute(
                    "SELECT id, symbol_key, kind, parent_id FROM nodes WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
                nodes = [
                    NodeRecord(row[0], row[1], NodeKind(row[2]), row[3])
                    for row in cursor.fetchall()
                ]
                cursor.execute(
                    "SELECT id, src_id, dst_id, kind, confidence FROM edges WHERE snapshot_id = %s",
                    (snapshot_id,),
                )
                edges = [
                    EdgeRecord(row[0], row[1], row[2], EdgeKind(row[3]), row[4])
                    for row in cursor.fetchall()
                ]
        return SnapshotGraph(snapshot_id, nodes, edges)


def _status(connection: Connection, snapshot_id: int) -> str | None:
    return cast(
        str | None,
        connection.execute(
            text("SELECT status FROM snapshots WHERE id = :s"), {"s": snapshot_id}
        ).scalar_one_or_none(),
    )


def _position(graph: SnapshotGraph, key: str) -> int:
    position = graph.index.get(key)
    if position is None:
        raise NodeNotFound(f"Snapshot {graph.snapshot_id} has no node {key!r}.")
    return position


def _edge_kinds(
    kinds: Iterable[EdgeKind | str] | None, default: Sequence[EdgeKind] | None = None
) -> list[EdgeKind]:
    """The chosen kinds, deduplicated, in vocabulary order."""
    if kinds is None:
        kinds = default if default is not None else list(EdgeKind)
    try:
        chosen = {EdgeKind(kind) for kind in kinds}
    except ValueError as error:
        raise InvalidQuery(f"Unknown edge kind: {error}") from None
    if not chosen:
        raise InvalidQuery("Name at least one edge kind.")
    return [kind for kind in EdgeKind if kind in chosen]


def _file_id(connection: Connection, snapshot_id: int, path: str) -> int:
    file_id = connection.execute(
        text("SELECT id FROM files WHERE snapshot_id = :s AND path = :path"),
        {"s": snapshot_id, "path": path},
    ).scalar_one_or_none()
    if file_id is None:
        raise PathNotInSnapshot(f"Snapshot {snapshot_id} has no file {path!r}.")
    return int(file_id)


def _nodes_by_id(connection: Connection, ids: Sequence[int]) -> dict[int, Node]:
    if not ids:
        return {}
    found = connection.execute(
        text(f"SELECT {rows.NODE_SELECT} FROM {rows.NODE_FROM} WHERE n.id = ANY(:ids)"),
        {"ids": list(ids)},
    )
    return {row.id: rows.node_from_row(row) for row in found}


def _edges_by_id(connection: Connection, ids: Sequence[int]) -> dict[int, Edge]:
    if not ids:
        return {}
    found = connection.execute(
        text(f"SELECT {rows.EDGE_SELECT} FROM {rows.EDGE_FROM} WHERE e.id = ANY(:ids)"),
        {"ids": list(ids)},
    )
    return {row.id: rows.edge_from_row(row) for row in found}


def _like_escape(value: str) -> str:
    """Escape ``LIKE`` wildcards (backslash is Postgres' default escape character)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
