"""Snapshot reads (API-5): metadata, subgraph, edge links, overview, node, locate, neighbors,
search, path, code.

Thin wrappers over ``GraphStore``. Every read except the snapshot's own metadata needs a ready
snapshot (409 ``snapshot_not_ready`` with the build job otherwise). Nodes are named by symbol
key; in a path segment the key is URL-encoded and decoded exactly once.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AfterValidator

from repoatlas.api.deps import EngineDep, ReadySnapshot, StoreDep, decode_key
from repoatlas.api.errors import documented, not_found
from repoatlas.api.schemas import (
    MAX_LINE,
    MAX_QUERY_CHARS,
    MAX_SEARCH_LIMIT,
    Degree,
    KeyPath,
    LineQuery,
    NodeDetail,
    RepoPathQuery,
    SearchResults,
    SnapshotIdPath,
    SnapshotView,
    key_query,
    no_nul,
)
from repoatlas.graph.model import EdgeKind, Node, NodeKind
from repoatlas.jobs.build import BUILD_JOB
from repoatlas.jobs.queue import latest_job
from repoatlas.store.cache import Direction, Level, SnapshotGraph
from repoatlas.store.graphstore import MAX_DEPTH, MAX_LINKS
from repoatlas.store.results import (
    CodeSlice,
    EdgeLinks,
    GraphPath,
    Neighborhood,
    Overview,
    Subgraph,
)

router = APIRouter(prefix="/snapshots/{snapshot_id}", tags=["snapshots"])
READ_ERRORS = documented(404, 409)

ParentKey = Annotated[
    str, key_query("A directory, file, or symbol key (default: the root)."), AfterValidator(no_nul)
]
FromKey = Annotated[
    str, key_query("Symbol key to start from.", alias="from"), AfterValidator(no_nul)
]
ToKey = Annotated[str, key_query("Symbol key to reach.", alias="to"), AfterValidator(no_nul)]
EdgeKinds = Annotated[
    list[EdgeKind] | None,
    Query(description="Edge kinds to follow, repeated (`kinds=calls&kinds=imports`)."),
]


@router.get("", summary="A snapshot", responses=documented(404))
def get_snapshot(snapshot_id: SnapshotIdPath, store: StoreDep, engine: EngineDep) -> SnapshotView:
    """The repository, commit, configuration fingerprint, status, build stats, error, and
    latest build job of a snapshot, whatever its status."""
    info = store.snapshot(snapshot_id)
    return SnapshotView.of(info, latest_job(snapshot_id, kind=BUILD_JOB, engine=engine))


@router.get("/subgraph", summary="Children and aggregated edges", responses=READ_ERRORS)
def get_subgraph(
    snapshot_id: ReadySnapshot,
    store: StoreDep,
    parent: ParentKey = "dir:.",
    level: Annotated[
        Level,
        Query(
            description="dir: subdirectories and files; file: every file below; "
            "symbol: the symbols directly inside a file or symbol."
        ),
    ] = "dir",
) -> Subgraph:
    """The children of ``parent`` at ``level`` and the edges between them, aggregated per
    (source child, destination child, kind) with counts and confidence ranges."""
    return store.subgraph(snapshot_id, parent, level)


@router.get("/links", summary="The edges behind an aggregated edge", responses=READ_ERRORS)
def get_edge_links(
    snapshot_id: ReadySnapshot,
    store: StoreDep,
    src: FromKey,
    dst: ToKey,
    kind: Annotated[EdgeKind, Query(description="The aggregated edge's kind.")],
    limit: Annotated[int, Query(ge=1, le=MAX_LINKS)] = 50,
) -> EdgeLinks:
    """Edges of ``kind`` from ``from`` (or a node below it) to ``to`` (or a node below it): the
    individual imports, calls, and links an aggregated edge in ``/subgraph`` counts, strongest
    first, with their call sites and both end nodes. ``total`` counts them all."""
    return store.edge_links(snapshot_id, src, dst, kind, limit)


@router.get("/overview", summary="Where to start reading", responses=READ_ERRORS)
def get_overview(snapshot_id: ReadySnapshot, store: StoreDep) -> Overview:
    """Public symbols and routes (outside test files), the files and symbols most used from other
    files (imports and calls; name-matched guesses left out), node counts by kind, and how
    many source files have linked tests."""
    return store.overview(snapshot_id)


@router.get("/nodes/{key}", summary="A node", responses=READ_ERRORS)
def get_node(snapshot_id: ReadySnapshot, key: KeyPath, store: StoreDep) -> NodeDetail:
    """A node, the owners of its file, and its edge counts by kind."""
    symbol = decode_key(key)
    found = store.get_node(snapshot_id, symbol)
    if found is None:
        raise not_found("node", f"Snapshot {snapshot_id} has no node {symbol!r}.")
    owners = [] if found.kind == NodeKind.DIR else store.owners(snapshot_id, found.path)
    graph = store.graph(snapshot_id)
    position = graph.index[found.key]
    return NodeDetail(
        node=found,
        owners=owners,
        degree=Degree(
            incoming=_by_kind(graph, graph.in_edges(position)),
            outgoing=_by_kind(graph, graph.out_edges(position)),
        ),
    )


@router.get("/nodes/{key}/neighbors", summary="Neighbors of a node", responses=READ_ERRORS)
def get_neighbors(
    snapshot_id: ReadySnapshot,
    key: KeyPath,
    store: StoreDep,
    direction: Annotated[Direction, Query(description="Follow edges in, out, or both.")] = "out",
    kinds: EdgeKinds = None,
    depth: Annotated[int, Query(ge=1, le=MAX_DEPTH, description="Hops to follow.")] = 1,
) -> Neighborhood:
    """Nodes within ``depth`` hops along edges of ``kinds`` (default: every kind), and the
    edges followed; at most 1,000 nodes (``truncated`` says when there were more)."""
    return store.neighbors(snapshot_id, decode_key(key), direction, kinds, depth)


@router.get("/locate", summary="The symbol at a line", responses=READ_ERRORS)
def locate_line(
    snapshot_id: ReadySnapshot, store: StoreDep, path: RepoPathQuery, line: LineQuery
) -> Node:
    """The innermost class, function, method, or test containing the line, else the file."""
    return store.locate(snapshot_id, path, line)


@router.get("/search", summary="Find symbols", responses=READ_ERRORS)
def search_symbols(
    snapshot_id: ReadySnapshot,
    store: StoreDep,
    q: Annotated[
        str,
        Query(
            min_length=1, max_length=MAX_QUERY_CHARS, description="Name, qualified name, or key."
        ),
        AfterValidator(no_nul),
    ],
    kinds: Annotated[
        list[NodeKind] | None, Query(description="Node kinds, repeated (`kinds=function`).")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = 20,
) -> SearchResults:
    """Symbols by exact name or key (any case), then prefix, then trigram similarity."""
    return SearchResults(query=q, results=store.find_symbols(snapshot_id, q, kinds, limit))


@router.get("/path", summary="A shortest path", responses=READ_ERRORS)
def find_path(
    snapshot_id: ReadySnapshot,
    store: StoreDep,
    src: FromKey,
    dst: ToKey,
    kinds: EdgeKinds = None,
) -> GraphPath:
    """A shortest path over ``kinds`` (default: calls and imports): directed when one exists,
    otherwise undirected (``directed`` is false). 404 when the nodes are not connected."""
    found = store.shortest_path(snapshot_id, src, dst, kinds)
    if found is None:
        raise not_found("path", f"No path connects {src!r} and {dst!r}.")
    return found


@router.get("/code", summary="Lines of a file", responses=READ_ERRORS)
def read_code(
    snapshot_id: ReadySnapshot,
    store: StoreDep,
    path: RepoPathQuery,
    start: Annotated[int, Query(ge=1, le=MAX_LINE, description="First line (1-based).")] = 1,
    end: Annotated[
        int | None, Query(ge=1, le=MAX_LINE, description="Last line (default: as many as allowed).")
    ] = None,
) -> CodeSlice:
    """Lines ``start`` to ``end`` of a file in the snapshot, from the stored text. At most
    ``max_read_lines`` (400) lines come back: ``end`` moves up, and past the file's end it
    becomes the last line. Only files of this snapshot can be read."""
    last = end if end is not None else start + store.max_read_lines - 1
    return store.read_code(snapshot_id, path, start, last)


def _by_kind(graph: SnapshotGraph, edges: Iterable[int]) -> dict[str, int]:
    counted = Counter(graph.edge_kind(edge) for edge in edges)
    return {kind.value: counted[kind] for kind in EdgeKind if counted[kind]}
