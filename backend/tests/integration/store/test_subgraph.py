"""GraphStore.subgraph (T5) on stored fixture snapshots."""

from __future__ import annotations

import pytest

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.store.cache import Level, SnapshotGraph
from repoatlas.store.errors import InvalidQuery, NodeNotFound
from repoatlas.store.results import AggregatedEdge
from tests.integration.store.conftest import Stored


def expected(stored: Stored, snapshot_id: int, parent: str, level: Level) -> list[AggregatedEdge]:
    snapshot = SnapshotGraph.from_code_graph(stored.graphs[snapshot_id])
    found = snapshot.aggregate(snapshot.index[parent], level)
    return [
        AggregatedEdge(
            src=snapshot.keys[link.src],
            dst=snapshot.keys[link.dst],
            kind=link.kind,
            count=link.count,
            min_confidence=link.min_confidence,
            max_confidence=link.max_confidence,
        )
        for link in found.links
    ]


def test_every_directory_and_file_matches_the_saved_graph(stored: Stored) -> None:
    for snapshot_id, graph in stored.graphs.items():
        for key, node in graph.nodes.items():
            if node.kind == NodeKind.DIR:
                levels: tuple[Level, ...] = ("dir", "file")
            elif node.kind == NodeKind.FILE:
                levels = ("symbol",)
            else:
                continue
            for level in levels:
                result = stored.store.subgraph(snapshot_id, key, level)
                assert result.parent == node and result.level == level
                assert result.edges == expected(stored, snapshot_id, key, level), (key, level)
                assert all(child == graph.nodes[child.key] for child in result.nodes)
                assert [child.key for child in result.nodes] == sorted(c.key for c in result.nodes)


def test_root_level_of_the_typescript_fixture(stored: Stored) -> None:
    result = stored.store.subgraph(stored.ts, "dir:.", "dir")
    assert [node.key for node in result.nodes] == ["dir:coverage", "dir:src", "dir:vendor"]
    assert result.edges == expected(stored, stored.ts, "dir:.", "dir")
    assert all(edge.kind != EdgeKind.CONTAINS for edge in result.edges)
    assert all(edge.count >= 1 for edge in result.edges)
    assert all(0 < edge.min_confidence <= edge.max_confidence <= 1 for edge in result.edges)


def test_subgraphs_are_memoized(stored: Stored) -> None:
    store = stored.store
    first = store.subgraph(stored.py, "dir:src/shop", "dir")
    graph = store.cache.peek(stored.py)
    assert graph is not None
    memo = graph.aggregate(graph.index["dir:src/shop"], "dir")
    assert store.subgraph(stored.py, "dir:src/shop", "dir") == first
    assert graph.aggregate(graph.index["dir:src/shop"], "dir") is memo


def test_bad_requests(stored: Stored) -> None:
    store = stored.store
    with pytest.raises(InvalidQuery, match="directory"):
        store.subgraph(stored.py, "file:src/shop/cart.py", "dir")
    with pytest.raises(InvalidQuery, match="not a directory"):
        store.subgraph(stored.py, "dir:src", "symbol")
    with pytest.raises(InvalidQuery, match="level"):
        store.subgraph(stored.py, "dir:src", "package")  # type: ignore[arg-type]
    with pytest.raises(NodeNotFound):
        store.subgraph(stored.py, "dir:nowhere", "dir")
