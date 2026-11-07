"""GraphStore node reads (T4): snapshots, lookups, search, traversals, paths, owners, unresolved."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlalchemy import Engine

from repoatlas.canonical import canonical_json
from repoatlas.graph.model import CodeGraph, Edge, EdgeKind, NodeKind
from repoatlas.store.cache import Direction
from repoatlas.store.errors import (
    InvalidQuery,
    NodeNotFound,
    PathNotInSnapshot,
    SnapshotNotFound,
    SnapshotNotReady,
)
from repoatlas.store.graphstore import GraphStore
from repoatlas.store.loader import begin_snapshot
from tests.integration.store.conftest import OWNERS, Stored
from tests.integration.store.helpers import FINGERPRINT, SHA_B, repo_meta

CART = "py:src/shop/cart.py:Cart:class"
CART_ADD = "py:src/shop/cart.py:Cart.add:method"
GIFT_ADD = "py:src/shop/cart.py:GiftCart.add:method"
DESCRIBE = "py:src/shop/cart.py:Cart.describe:method"
ROUND = "py:src/shop/utils/helpers.py:round_money:function"
FORMAT = "py:src/shop/utils/helpers.py:format_price:function"
GET_CART = "py:src/shop/app.py:get_cart:function"
ADD_ITEM = "py:src/shop/app.py:add_item:function"
PING, PONG, FACT = (
    "py:loop/a.py:ping:function",
    "py:loop/b.py:pong:function",
    "py:loop/a.py:fact:function",
)


def reference(
    graph: CodeGraph, key: str, direction: Direction, kinds: Iterable[EdgeKind], depth: int
) -> tuple[dict[str, int], list[str]]:
    """A naive breadth-first search straight over ``graph.edges``, to check the store against."""
    chosen = set(kinds)
    depths = {key: 0}
    followed: dict[str, Edge] = {}
    frontier = [key]
    for level in range(1, depth + 1):
        reached = []
        for node in frontier:
            for edge in graph.edges:
                if edge.kind not in chosen:
                    continue
                ends = []
                if direction != "in" and edge.src == node:
                    ends.append(edge.dst)
                if direction != "out" and edge.dst == node:
                    ends.append(edge.src)
                for neighbor in ends:
                    if neighbor not in depths:
                        depths[neighbor] = level
                        reached.append(neighbor)
                    followed[canonical_json(edge)] = edge
        frontier = reached
    depths.pop(key)
    return depths, sorted(followed)


def test_snapshot_info(stored: Stored) -> None:
    info = stored.store.snapshot(stored.py)
    assert (info.repo.owner, info.repo.name, info.repo.host) == ("acme", "py-small", "github.com")
    assert (info.status, info.error, info.config_fingerprint) == ("ready", None, FINGERPRINT)
    assert info.stats["graph_digest"] == stored.graphs[stored.py].digest()
    with pytest.raises(SnapshotNotFound):
        stored.store.snapshot(999_999)


def test_reads_need_a_ready_snapshot(stored: Stored, pg_engine: Engine) -> None:
    building = begin_snapshot(repo_meta("acme", "unfinished"), SHA_B, FINGERPRINT, engine=pg_engine)
    store = GraphStore(pg_engine)
    assert store.snapshot(building.id).status == "building"
    for read in (
        lambda: store.get_node(building.id, CART),
        lambda: store.callers(building.id, CART),
        lambda: store.find_symbols(building.id, "Cart"),
        lambda: store.owners(building.id, "src/shop/cart.py"),
    ):
        with pytest.raises(SnapshotNotReady):
            read()
    assert building.id not in store.cache  # nothing was cached for it
    with pytest.raises(SnapshotNotFound):
        store.get_node(999_999, CART)


def test_get_node_returns_the_saved_node(stored: Stored) -> None:
    for snapshot_id, graph in stored.graphs.items():
        for key, node in graph.nodes.items():
            assert stored.store.get_node(snapshot_id, key) == node
    assert stored.store.get_node(stored.py, "py:src/shop/cart.py:Nope:class") is None
    assert stored.store.get_node(stored.ts, CART) is None  # keys are per snapshot


def test_search_ranks_exact_then_prefix_then_fuzzy(stored: Stored) -> None:
    def search(query: str, **kwargs: object) -> list[tuple[str, str]]:
        found = stored.store.find_symbols(stored.py, query, **kwargs)  # type: ignore[arg-type]
        return [(match.node.key, match.match) for match in found]

    assert search("Cart")[0] == (CART, "exact")
    assert search("cart")[:2] == [("py:tests/conftest.py:cart:function", "exact"), (CART, "exact")]
    assert search(CART_ADD)[0] == (CART_ADD, "exact")  # a full key; similar keys follow
    assert search("Cart.add")[0] == (CART_ADD, "exact")
    assert search("format", kinds=[NodeKind.FUNCTION]) == [
        (FORMAT, "prefix"),
        ("py:src/shop/utils/helpers.py:format_price.with_symbol:function", "prefix"),
    ]
    assert search("round_mony") == [(ROUND, "fuzzy")]
    assert search("add", kinds=["method"]) == [(CART_ADD, "exact"), (GIFT_ADD, "exact")]
    assert search("add", kinds=[NodeKind.FUNCTION]) == [(ADD_ITEM, "prefix")]
    assert search("_disc") == [("py:src/shop/cart.py:Cart._discount:method", "prefix")]
    assert search("%") == [] and search("_") != search("%")  # wildcards are literal
    assert len(search("a", limit=3)) == 3
    exact = stored.store.find_symbols(stored.py, "Cart")[0]
    assert exact.score == 1.0 and exact.node == stored.graphs[stored.py].nodes[CART]


def test_search_rejects_bad_requests(stored: Stored) -> None:
    for query, limit in (("  ", 20), ("Cart", 0), ("Cart", 101)):
        with pytest.raises(InvalidQuery):
            stored.store.find_symbols(stored.py, query, limit=limit)
    with pytest.raises(InvalidQuery, match="node kind"):
        stored.store.find_symbols(stored.py, "Cart", kinds=["module"])


@pytest.mark.parametrize("depth", [1, 3])
def test_traversals_match_a_naive_search_for_every_node(stored: Stored, depth: int) -> None:
    store = stored.store
    for snapshot_id, graph in stored.graphs.items():
        for key in graph.nodes:
            for direction, kinds, result in (
                ("in", [EdgeKind.CALLS], store.callers(snapshot_id, key, depth)),
                ("out", [EdgeKind.CALLS], store.callees(snapshot_id, key, depth)),
                ("both", list(EdgeKind), store.neighbors(snapshot_id, key, "both", None, depth)),
            ):
                depths, edges = reference(graph, key, direction, kinds, depth)  # type: ignore[arg-type]
                assert {hop.node.key: hop.depth for hop in result.nodes} == depths, (key, direction)
                assert sorted(canonical_json(edge) for edge in result.edges) == edges
                assert result.root == graph.nodes[key] and not result.truncated
                assert all(hop.node == graph.nodes[hop.node.key] for hop in result.nodes)


def test_callers_by_depth(stored: Stored) -> None:
    def callers(key: str, depth: int) -> dict[str, int]:
        return {
            hop.node.key: hop.depth for hop in stored.store.callers(stored.py, key, depth).nodes
        }

    assert callers(ROUND, 1) == {CART_ADD: 1, FORMAT: 1}
    third = callers(ROUND, 3)
    assert third[DESCRIBE] == 2 and third[GET_CART] == 3 and third[ADD_ITEM] == 2
    result = stored.store.callers(stored.py, ROUND, 3)
    assert [hop.depth for hop in result.nodes] == sorted(hop.depth for hop in result.nodes)
    assert (result.direction, result.kinds, result.depth) == ("in", [EdgeKind.CALLS], 3)
    callees = stored.store.callees(stored.py, GET_CART, 1)
    assert {hop.node.key for hop in callees.nodes} == {
        CART,
        "py:src/shop/cart.py:Cart.__init__:method",
        DESCRIBE,
    }
    assert {edge.meta["tier"] for edge in callees.edges} == {"T1", "T5"}


def test_traversals_end_on_cycles(stored: Stored) -> None:
    store = stored.store
    ping = store.callers(stored.loop, PING, 20)
    assert {hop.node.key: hop.depth for hop in ping.nodes} == {PONG: 1}
    assert {(edge.src, edge.dst) for edge in ping.edges} == {(PING, PONG), (PONG, PING)}
    fact = store.callees(stored.loop, FACT, 20)
    assert fact.nodes == [] and [(e.src, e.dst) for e in fact.edges] == [(FACT, FACT)]
    imports = store.neighbors(stored.loop, "file:loop/a.py", "out", [EdgeKind.IMPORTS], 20)
    assert [hop.node.key for hop in imports.nodes] == ["file:loop/b.py"]
    assert {(e.src, e.dst) for e in imports.edges} == {
        ("file:loop/a.py", "file:loop/b.py"),
        ("file:loop/b.py", "file:loop/a.py"),
    }


def test_the_node_cap_truncates(stored: Stored, pg_engine: Engine) -> None:
    capped = GraphStore(pg_engine, stored.store.cache, max_nodes=2)
    result = capped.callers(stored.py, ROUND, 3)
    assert result.truncated and len(result.nodes) == 2
    keys = {result.root.key, *(hop.node.key for hop in result.nodes)}
    assert all(edge.src in keys and edge.dst in keys for edge in result.edges)


def test_traversals_reject_bad_requests(stored: Stored) -> None:
    store = stored.store
    with pytest.raises(NodeNotFound):
        store.callers(stored.py, "py:nowhere.py:f:function")
    for depth in (0, 21):
        with pytest.raises(InvalidQuery):
            store.callers(stored.py, ROUND, depth)
    with pytest.raises(InvalidQuery):
        store.neighbors(stored.py, ROUND, "up")  # type: ignore[arg-type]
    for kinds in (["uses"], []):
        with pytest.raises(InvalidQuery):
            store.neighbors(stored.py, ROUND, "in", kinds)


def test_shortest_path(stored: Stored) -> None:
    store = stored.store

    def keys(src: str, dst: str, kinds: list[EdgeKind] | None = None) -> tuple[list[str], bool]:
        found = store.shortest_path(stored.py, src, dst, kinds)
        assert found is not None
        for position, edge in enumerate(found.edges):
            pair = {found.nodes[position].key, found.nodes[position + 1].key}
            assert {edge.src, edge.dst} == pair
        return [node.key for node in found.nodes], found.directed

    assert keys(GET_CART, ROUND) == ([GET_CART, DESCRIBE, FORMAT, ROUND], True)
    assert keys(ROUND, GET_CART) == ([ROUND, FORMAT, DESCRIBE, GET_CART], False)
    assert keys("file:src/shop/app.py", "file:src/shop/utils/helpers.py") == (
        ["file:src/shop/app.py", "file:src/shop/cart.py", "file:src/shop/utils/helpers.py"],
        True,
    )
    assert keys(CART, CART) == ([CART], True)
    found = store.shortest_path(stored.py, GET_CART, ROUND)
    assert found is not None and found.kinds == [EdgeKind.IMPORTS, EdgeKind.CALLS]
    route = "route:src/shop/app.py:GET /carts/{cart_id}"
    assert store.shortest_path(stored.py, route, ROUND, [EdgeKind.CALLS]) is None
    assert keys(route, ROUND, [EdgeKind.ROUTES_TO, EdgeKind.CALLS])[0] == [
        route,
        GET_CART,
        DESCRIBE,
        FORMAT,
        ROUND,
    ]
    with pytest.raises(NodeNotFound):
        store.shortest_path(stored.py, GET_CART, "py:x.py:y:function")


def test_owners(stored: Stored) -> None:
    assert stored.store.owners(stored.py, "src/shop/cart.py") == OWNERS["src/shop/cart.py"]
    assert stored.store.owners(stored.py, "src/shop/app.py") == []
    with pytest.raises(PathNotInSnapshot):
        stored.store.owners(stored.py, "src/shop/missing.py")


def test_unresolved_calls_by_name(stored: Stored) -> None:
    graph = stored.graphs[stored.py]
    by_name = stored.store.unresolved_calls(stored.py, "append")
    assert [call.callee_text for call in by_name] == ["self.items.append"]
    assert by_name == [call for call in graph.unresolved if call.callee_text == "self.items.append"]
    assert [call.callee_text for call in stored.store.unresolved_calls(stored.py, "app.get")] == [
        "app.get"
    ]
    ts_get = stored.store.unresolved_calls(stored.ts, "get")
    assert sorted(call.callee_text for call in ts_get) == ["app.get", "carts.get", "carts.get"]
    assert stored.store.unresolved_calls(stored.py, "nothing_like_this") == []


def test_the_adjacency_is_cached_after_the_first_traversal(
    stored: Stored, pg_engine: Engine
) -> None:
    store = GraphStore(pg_engine)
    assert stored.py not in store.cache
    store.callers(stored.py, ROUND)
    graph = store.cache.peek(stored.py)
    assert graph is not None and graph.node_count == len(stored.graphs[stored.py].nodes)
    assert graph.edge_count == len(stored.graphs[stored.py].edges)
    store.callees(stored.py, ROUND)
    assert store.cache.hits == 1 and store.cache.misses == 1
