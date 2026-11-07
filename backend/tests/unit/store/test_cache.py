"""SnapshotGraph adjacency and traversals (T3): depth, kinds, directions, and cycles."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterable

import pytest

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.store.cache import (
    DIRECTIONS,
    EdgeRecord,
    NodeRecord,
    SnapshotGraph,
    SnapshotGraphCache,
)
from tests.unit.graph.helpers import graph_of

CALLS, IMPORTS, CONTAINS = EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.CONTAINS
Spec = tuple[str, str] | tuple[str, str, EdgeKind]


def make(
    edges: Iterable[Spec],
    *,
    nodes: Iterable[str] = (),
    snapshot_id: int = 1,
    seed: int | None = None,
) -> SnapshotGraph:
    """A graph of function nodes named by ``edges`` (``calls`` unless a kind is given)."""
    specs = [(edge[0], edge[1], edge[2] if len(edge) == 3 else CALLS) for edge in edges]
    names = sorted({*nodes, *(s for s, _, _ in specs), *(d for _, d, _ in specs)})
    ids = {name: 100 + position for position, name in enumerate(names)}
    node_records = [NodeRecord(ids[name], name, NodeKind.FUNCTION, None) for name in names]
    edge_records = [
        EdgeRecord(1_000 + position, ids[src], ids[dst], kind, 0.5)
        for position, (src, dst, kind) in enumerate(specs)
    ]
    if seed is not None:  # the input order must not matter
        random.Random(seed).shuffle(node_records)
        random.Random(seed).shuffle(edge_records)
    return SnapshotGraph(snapshot_id, node_records, edge_records)


def reached(graph: SnapshotGraph, start: str, **kwargs: object) -> dict[str, int]:
    result = graph.traverse(graph.index[start], **kwargs)  # type: ignore[arg-type]
    return {
        graph.keys[node]: depth for node, depth in result.depths.items() if node != result.start
    }


def followed(graph: SnapshotGraph, start: str, **kwargs: object) -> list[tuple[str, str]]:
    result = graph.traverse(graph.index[start], **kwargs)  # type: ignore[arg-type]
    return [(graph.keys[graph.edge_src[e]], graph.keys[graph.edge_dst[e]]) for e in result.edges]


def path(
    graph: SnapshotGraph, src: str, dst: str, **kwargs: object
) -> tuple[list[str], bool] | None:
    found = graph.shortest_path(graph.index[src], graph.index[dst], **kwargs)  # type: ignore[arg-type]
    if found is None:
        return None
    return [graph.keys[node] for node in found.nodes], found.directed


def test_adjacency_lists_are_per_kind_and_in_key_order() -> None:
    graph = make([("a", "c"), ("a", "b"), ("a", "d", IMPORTS), ("b", "a"), ("c", "a")], seed=3)
    a = graph.index["a"]
    out = [(graph.keys[graph.edge_dst[e]], graph.edge_kind(e)) for e in graph.out_edges(a)]
    assert out == [("d", IMPORTS), ("b", CALLS), ("c", CALLS)]  # by kind, then by key
    assert [graph.keys[graph.edge_dst[e]] for e in graph.out_edges(a, [CALLS])] == ["b", "c"]
    assert [graph.keys[graph.edge_dst[e]] for e in graph.out_edges(a, [IMPORTS])] == ["d"]
    assert [graph.keys[graph.edge_src[e]] for e in graph.in_edges(a)] == ["b", "c"]
    assert graph.in_edges(graph.index["d"], [CALLS]) == []
    assert (graph.node_count, graph.edge_count) == (4, 5)
    assert sorted(graph.edge_ids) == [1_000, 1_001, 1_002, 1_003, 1_004]


def test_depth_limits_the_search() -> None:
    graph = make([("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")])
    assert reached(graph, "a", direction="out", depth=1) == {"b": 1}
    assert reached(graph, "a", direction="out", depth=3) == {"b": 1, "c": 2, "d": 3}
    assert reached(graph, "e", direction="in", depth=2) == {"d": 1, "c": 2}
    assert followed(graph, "a", direction="out", depth=2) == [("a", "b"), ("b", "c")]


def test_nodes_are_reached_at_their_smallest_depth() -> None:
    graph = make([("a", "b"), ("b", "c"), ("a", "c"), ("c", "d")])
    assert reached(graph, "a", direction="out", depth=5) == {"b": 1, "c": 1, "d": 2}
    assert set(followed(graph, "a", direction="out", depth=5)) == {
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
        ("c", "d"),
    }


def test_kinds_filter_and_directions() -> None:
    graph = make([("a", "b"), ("a", "c", IMPORTS), ("d", "a"), ("e", "a", IMPORTS)])
    assert reached(graph, "a", direction="out", kinds=[CALLS]) == {"b": 1}
    assert reached(graph, "a", direction="out", kinds=[IMPORTS]) == {"c": 1}
    assert reached(graph, "a", direction="out") == {"b": 1, "c": 1}
    assert reached(graph, "a", direction="in", kinds=[CALLS]) == {"d": 1}
    assert reached(graph, "a", direction="both", kinds=[CALLS, IMPORTS]) == {
        "b": 1,
        "c": 1,
        "d": 1,
        "e": 1,
    }
    assert reached(graph, "a", direction="out", kinds=[CONTAINS]) == {}
    with pytest.raises(ValueError, match="direction"):
        graph.traverse(0, direction="sideways", depth=1)  # type: ignore[arg-type]


def test_a_self_loop_is_followed_once() -> None:
    graph = make([("a", "a"), ("a", "b")])
    for direction in DIRECTIONS:
        result = graph.traverse(graph.index["a"], direction=direction, depth=10)
        assert result.depths[graph.index["a"]] == 0
        assert len(result.edges) == len(set(result.edges))
    assert reached(graph, "a", direction="out", depth=10) == {"b": 1}
    assert followed(graph, "a", direction="both", depth=10) == [("a", "a"), ("a", "b")]


def test_mutual_recursion_terminates() -> None:
    graph = make([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")])
    assert reached(graph, "a", direction="out", depth=50) == {"b": 1, "c": 2}
    assert reached(graph, "a", direction="in", depth=50) == {"b": 1, "c": 2}
    assert set(followed(graph, "a", direction="out", depth=50)) == {
        ("a", "b"),
        ("b", "a"),
        ("b", "c"),
        ("c", "b"),
    }
    assert path(graph, "c", "a") == (["c", "b", "a"], True)


def test_a_large_cycle_terminates_and_paths_go_around_it() -> None:
    size = 10_000
    names = [f"n{position:05d}" for position in range(size)]
    graph = make([(names[i], names[(i + 1) % size]) for i in range(size)])
    started = time.perf_counter()
    result = graph.traverse(graph.index[names[0]], direction="out", depth=size * 2)
    assert len(result.depths) == size and len(result.edges) == size
    assert result.depths[graph.index[names[-1]]] == size - 1
    assert reached(graph, names[0], direction="in", depth=3) == {
        names[-1]: 1,
        names[-2]: 2,
        names[-3]: 3,
    }
    forward = path(graph, names[0], names[1])
    backward = path(graph, names[1], names[0])
    assert forward == ([names[0], names[1]], True)
    assert backward is not None and len(backward[0]) == size and backward[1] is True
    assert time.perf_counter() - started < 5


def test_max_nodes_truncates_deterministically() -> None:
    graph = make([("root", f"c{i}") for i in range(10)] + [("c0", "d0")])
    result = graph.traverse(graph.index["root"], direction="out", depth=2, max_nodes=3)
    assert result.truncated
    assert sorted(graph.keys[n] for n in result.depths) == ["c0", "c1", "c2", "root"]
    assert all(
        graph.edge_dst[e] in result.depths and graph.edge_src[e] in result.depths
        for e in result.edges
    )
    complete = graph.traverse(graph.index["root"], direction="out", depth=2, max_nodes=11)
    assert not complete.truncated and len(complete.depths) == 12


def test_shortest_path_prefers_directed_then_falls_back_to_undirected() -> None:
    graph = make([("a", "b"), ("c", "b"), ("b", "d"), ("d", "e"), ("a", "e", IMPORTS)])
    assert path(graph, "a", "e") == (["a", "e"], True)
    assert path(graph, "a", "e", kinds=[CALLS]) == (["a", "b", "d", "e"], True)
    assert path(graph, "a", "c") == (["a", "b", "c"], False)
    assert path(graph, "e", "a", kinds=[CALLS]) == (["e", "d", "b", "a"], False)
    assert path(graph, "a", "a") == (["a"], True)
    lonely = make([("a", "b")], nodes=["z"])
    assert path(lonely, "a", "z") is None


def test_path_edges_link_consecutive_nodes() -> None:
    graph = make([("a", "b"), ("c", "b"), ("c", "d")])
    found = graph.shortest_path(graph.index["a"], graph.index["d"])
    assert found is not None and not found.directed
    for position, edge in enumerate(found.edges):
        ends = {graph.edge_src[edge], graph.edge_dst[edge]}
        assert ends == {found.nodes[position], found.nodes[position + 1]}


def test_results_do_not_depend_on_input_order() -> None:
    edges: list[Spec] = [(f"f{i % 17}", f"f{(i * 7) % 23}") for i in range(200)]
    edges += [(f"f{i}", f"f{i + 1}", IMPORTS) for i in range(16)]
    reference = make(edges)
    for seed in (1, 2, 3):
        shuffled = make(edges, seed=seed)
        assert shuffled.keys == reference.keys
        assert list(shuffled.edge_ids) != [] and shuffled.edge_kinds == reference.edge_kinds
        for start in ("f0", "f5", "f16"):
            for direction in ("in", "out", "both"):
                assert followed(shuffled, start, direction=direction, depth=3) == followed(
                    reference, start, direction=direction, depth=3
                )
        assert path(shuffled, "f3", "f20") == path(reference, "f3", "f20")


def test_bad_references_are_rejected() -> None:
    with pytest.raises(ValueError, match="edge destination 7"):
        SnapshotGraph(
            1, [NodeRecord(1, "a", NodeKind.FUNCTION, None)], [EdgeRecord(1, 1, 7, CALLS, 1.0)]
        )
    with pytest.raises(ValueError, match="parent 9"):
        SnapshotGraph(1, [NodeRecord(1, "a", NodeKind.FUNCTION, 9)], [])
    with pytest.raises(ValueError, match="duplicate"):
        SnapshotGraph(1, [NodeRecord(1, "a", NodeKind.FUNCTION, None)] * 2, [])


def test_from_code_graph_matches_the_graph() -> None:
    graph = graph_of(
        {
            "pkg/a.py": "from pkg.b import g\n\ndef f():\n    g()\n    f()\n",
            "pkg/b.py": "def g():\n    pass\n",
        }
    )
    snapshot = SnapshotGraph.from_code_graph(graph, snapshot_id=7)
    assert snapshot.snapshot_id == 7 and snapshot.keys == list(graph.nodes)
    f = snapshot.index["py:pkg/a.py:f:function"]
    assert reached(snapshot, "py:pkg/a.py:f:function", direction="out", kinds=[CALLS]) == {
        "py:pkg/b.py:g:function": 1
    }
    assert any(snapshot.edge_dst[e] == f for e in snapshot.out_edges(f, [CALLS]))  # recursion
    assert snapshot.kind_of(snapshot.index["file:pkg/a.py"]) == NodeKind.FILE
    parent = snapshot.parents[f]
    assert snapshot.keys[parent] == "file:pkg/a.py"
    assert snapshot.parents[snapshot.index["dir:."]] == -1
    assert snapshot.edge_count == len(graph.edges)


def test_estimated_size_grows_with_the_graph() -> None:
    small = make([("a", "b")])
    large = make([(f"n{i}", f"n{i + 1}") for i in range(1_000)])
    assert 0 < small.estimated_bytes < large.estimated_bytes
    assert large.estimated_bytes > 1_000 * (8 + 4 + 4 + 8 + 8)


def _sized(snapshot_id: int, nodes: int) -> SnapshotGraph:
    return make([(f"n{i}", f"n{i + 1}") for i in range(nodes)], snapshot_id=snapshot_id)


def test_cache_evicts_the_least_recently_used_snapshot_past_the_cap() -> None:
    graphs = {sid: _sized(sid, 2_000) for sid in (1, 2, 3)}
    size = graphs[1].estimated_bytes
    cache = SnapshotGraphCache(max_mb=(size * 2.5) / (1024 * 1024))
    loads: list[int] = []

    def loader(sid: int) -> Callable[[], SnapshotGraph]:
        def load() -> SnapshotGraph:
            loads.append(sid)
            return graphs[sid]

        return load

    assert cache.get(1, loader(1)) is graphs[1]
    assert cache.get(2, loader(2)) is graphs[2]
    assert cache.get(1, loader(1)) is graphs[1]  # hit: 1 is now the most recent
    assert cache.get(3, loader(3)) is graphs[3]  # evicts 2, the least recently used
    assert cache.snapshot_ids() == [1, 3]
    assert loads == [1, 2, 3]
    assert (cache.hits, cache.misses, cache.evictions) == (1, 3, 1)
    assert 2 not in cache and len(cache) == 2 and cache.size_bytes <= cache.max_bytes
    assert cache.peek(2) is None and cache.peek(1) is graphs[1]


def test_cache_keeps_the_newest_entry_even_when_it_alone_is_too_big() -> None:
    cache = SnapshotGraphCache(max_mb=0.001)
    big = _sized(5, 5_000)
    assert cache.get(5, lambda: big) is big
    assert cache.snapshot_ids() == [5]
    small = _sized(6, 10)
    cache.get(6, lambda: small)
    assert cache.snapshot_ids() == [6]


def test_cache_loads_each_snapshot_once_under_concurrency() -> None:
    cache = SnapshotGraphCache()
    calls = 0
    lock = threading.Lock()

    def slow_load() -> SnapshotGraph:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return _sized(9, 100)

    results: list[SnapshotGraph] = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.get(9, slow_load))) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == 1 and len(results) == 8 and len({id(graph) for graph in results}) == 1


def test_cache_rejects_a_loader_that_returns_another_snapshot() -> None:
    cache = SnapshotGraphCache()
    with pytest.raises(ValueError, match="not 2"):
        cache.get(2, lambda: _sized(3, 1))
    assert 2 not in cache
    with pytest.raises(ValueError):
        SnapshotGraphCache(max_mb=0)


def test_a_failed_load_is_retried_next_time() -> None:
    cache = SnapshotGraphCache()

    def fail() -> SnapshotGraph:
        raise RuntimeError("database down")

    with pytest.raises(RuntimeError):
        cache.get(4, fail)
    graph = _sized(4, 3)
    assert cache.get(4, lambda: graph) is graph
    cache.discard(4)
    assert 4 not in cache
    cache.get(4, lambda: graph)
    cache.clear()
    assert len(cache) == 0
