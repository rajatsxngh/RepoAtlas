"""GraphStore.edge_links and GraphStore.overview on stored fixture snapshots."""

from __future__ import annotations

import pytest

from repoatlas.graph.model import CodeGraph, EdgeKind, NodeKind
from repoatlas.store.cache import SnapshotGraph
from repoatlas.store.errors import InvalidQuery
from tests.integration.store.conftest import Stored


def _below(graph: CodeGraph, key: str, ancestor: str) -> bool:
    while key != ancestor:
        parent = graph.nodes[key].parent_key
        if parent is None:
            return False
        key = parent
    return True


def test_links_explain_every_aggregated_edge(stored: Stored) -> None:
    for snapshot_id, graph in stored.graphs.items():
        for parent in ("dir:.", *(k for k, n in graph.nodes.items() if n.kind == NodeKind.DIR)):
            for aggregated in stored.store.subgraph(snapshot_id, parent, "file").edges:
                found = stored.store.edge_links(
                    snapshot_id, aggregated.src, aggregated.dst, aggregated.kind, limit=200
                )
                assert found.total == aggregated.count == len(found.links)
                assert all(link.kind == aggregated.kind for link in found.links)
                assert all(_below(graph, link.src, aggregated.src) for link in found.links)
                assert all(_below(graph, link.dst, aggregated.dst) for link in found.links)
                confidences = [link.confidence for link in found.links]
                assert confidences == sorted(confidences, reverse=True)
                assert {link.src for link in found.links} | {link.dst for link in found.links} == {
                    node.key for node in found.nodes
                }


def test_links_respect_the_limit_and_validate_it(stored: Stored) -> None:
    edges = stored.store.subgraph(stored.py, "dir:.", "file").edges
    heaviest = max(edges, key=lambda edge: edge.count)
    found = stored.store.edge_links(stored.py, heaviest.src, heaviest.dst, heaviest.kind, limit=1)
    assert len(found.links) == 1 and found.total == heaviest.count
    with pytest.raises(InvalidQuery):
        stored.store.edge_links(stored.py, heaviest.src, heaviest.dst, heaviest.kind, limit=0)


def test_overview_of_the_python_fixture(stored: Stored) -> None:
    graph = stored.graphs[stored.py]
    found = stored.store.overview(stored.py)
    kinds = [node.kind.value for node in graph.nodes.values()]
    assert found.counts == {kind: kinds.count(kind) for kind in set(kinds)}

    def in_test_file(key: str) -> bool:
        return bool(graph.nodes[f"file:{graph.nodes[key].path}"].meta.get("is_test"))

    routes = sorted(
        key
        for key, node in graph.nodes.items()
        if node.kind == NodeKind.ROUTE and not in_test_file(key)
    )
    assert routes and [node.key for node in found.routes] == routes
    public = sorted(
        key
        for key, node in graph.nodes.items()
        if node.kind in (NodeKind.CLASS, NodeKind.FUNCTION) and node.meta.get("public") is True
    )
    assert sorted(node.key for node in found.public_symbols) == sorted(public[:16])
    snapshot = SnapshotGraph.from_code_graph(graph)
    _, per_symbol, _ = snapshot.cross_file_uses((EdgeKind.IMPORTS, EdgeKind.CALLS), 0.6)
    ranks = [per_symbol.get(snapshot.index[node.key], 0) for node in found.public_symbols]
    assert ranks == sorted(ranks, reverse=True)
    assert found.core_files, "the fixture's files import each other"
    assert all(item.node.kind == NodeKind.FILE for item in found.core_files)
    assert all(item.node.kind not in (NodeKind.FILE, NodeKind.DIR) for item in found.core_symbols)
    uses = [item.uses for item in found.core_files]
    assert uses == sorted(uses, reverse=True)
    assert all(1 <= item.files <= item.uses for item in found.core_files + found.core_symbols)
    test_files = {
        key
        for key, node in graph.nodes.items()
        if node.kind == NodeKind.FILE and node.meta.get("is_test")
    }
    files = [key for key, node in graph.nodes.items() if node.kind == NodeKind.FILE]
    assert found.source_files == len(files) - len(test_files)
    tested = {e.src for e in graph.edges if e.kind == EdgeKind.TESTED_BY}
    assert 0 < found.tested_files <= found.source_files
    assert found.tested_files <= len(tested)


def test_overview_ignores_guessed_calls(stored: Stored) -> None:
    graph = stored.graphs[stored.ts]
    found = stored.store.overview(stored.ts)
    strong_targets = {
        e.dst
        for e in graph.edges
        if e.kind in (EdgeKind.IMPORTS, EdgeKind.CALLS) and e.confidence >= 0.6
    }
    assert all(item.node.key in strong_targets for item in found.core_symbols)
