"""Graph expansion on the fixture graph and on small synthetic graphs (RET-6)."""

from __future__ import annotations

import math

import pytest

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.retrieval.expansion import HOP_DECAY, Seed, expand
from repoatlas.store.cache import EdgeRecord, NodeRecord, SnapshotGraph
from tests.unit.retrieval.helpers import fixture_graph

ADD = "py:src/shop/cart.py:Cart.add:method"
CART = "py:src/shop/cart.py:Cart:class"
GIFT_ADD = "py:src/shop/cart.py:GiftCart.add:method"
ROUND = "py:src/shop/utils/helpers.py:round_money:function"
FORMAT = "py:src/shop/utils/helpers.py:format_price:function"
DESCRIBE = "py:src/shop/cart.py:Cart.describe:method"


@pytest.fixture(scope="module")
def py_graph() -> SnapshotGraph:
    return SnapshotGraph.from_code_graph(fixture_graph("py_small"))


def synthetic(
    edges: list[tuple[str, str, float]], kind: EdgeKind = EdgeKind.CALLS
) -> SnapshotGraph:
    keys = sorted({key for edge in edges for key in edge[:2]})
    ids = {key: number for number, key in enumerate(keys, start=1)}
    return SnapshotGraph(
        1,
        [NodeRecord(ids[key], key, NodeKind.FUNCTION, None) for key in keys],
        [
            EdgeRecord(number, ids[src], ids[dst], kind, confidence)
            for number, (src, dst, confidence) in enumerate(edges, start=1)
        ],
    )


def test_direct_neighbors_score_seed_times_confidence_times_decay(py_graph: SnapshotGraph) -> None:
    found = {item.key: item for item in expand(py_graph, [Seed(ADD, 1.0)])}
    rounded = found[ROUND]
    assert (rounded.hops, rounded.via, rounded.direction, rounded.seed) == (
        1,
        EdgeKind.CALLS,
        "out",
        ADD,
    )
    assert math.isclose(rounded.score, 0.95 * HOP_DECAY)
    caller = found[GIFT_ADD]  # GiftCart.add calls Cart.add: followed backwards
    assert (caller.hops, caller.direction) == (1, "in")
    assert math.isclose(caller.score, 0.8 * HOP_DECAY)
    parent = found[CART]  # contains, backwards
    assert (parent.via, parent.direction, parent.score) == (EdgeKind.CONTAINS, "in", 0.5)


def test_scores_decay_per_hop_and_multiply_confidences(py_graph: SnapshotGraph) -> None:
    found = {item.key: item for item in expand(py_graph, [Seed(ADD, 2.0)])}
    sibling = found[DESCRIBE]  # Cart.add ← Cart (contains) → Cart.describe
    assert sibling.hops == 2
    assert math.isclose(sibling.score, 2.0 * 1.0 * 1.0 * HOP_DECAY**2)
    for item in found.values():
        assert item.hops in (1, 2)
        assert math.isclose(item.score, 2.0 * item.confidence * HOP_DECAY**item.hops)
    assert ADD not in found  # the seed itself is not an expanded result


def test_one_hop_stops_at_direct_neighbors(py_graph: SnapshotGraph) -> None:
    found = expand(py_graph, [Seed(ADD, 1.0)], max_hops=1)
    assert found and all(item.hops == 1 for item in found)
    assert DESCRIBE not in {item.key for item in found}


def test_a_strong_two_hop_path_beats_a_weak_direct_edge() -> None:
    graph = synthetic([("a", "b", 0.2), ("a", "c", 1.0), ("c", "b", 1.0)])
    found = {item.key: item for item in expand(graph, [Seed("a", 1.0)])}
    assert math.isclose(found["b"].score, 0.25) and found["b"].hops == 2
    assert math.isclose(found["c"].score, 0.5)


def test_nodes_reached_from_several_seeds_keep_their_best_score(py_graph: SnapshotGraph) -> None:
    found = expand(py_graph, [Seed(ADD, 1.0), Seed(FORMAT, 0.5)])
    keys = [item.key for item in found]
    assert len(keys) == len(set(keys))  # deduplicated by node
    by_key = {item.key: item for item in found}
    assert by_key[ROUND].seed == ADD and math.isclose(by_key[ROUND].score, 0.95 * 0.5)
    assert FORMAT not in by_key and ADD not in by_key  # seeds are never expanded results
    weaker_first = expand(py_graph, [Seed(FORMAT, 0.5), Seed(ADD, 1.0)])
    assert weaker_first == found  # the order of seeds does not matter


def test_results_are_sorted_and_deterministic(py_graph: SnapshotGraph) -> None:
    found = expand(py_graph, [Seed(ADD, 1.0)])
    assert [(-i.score, i.key) for i in found] == sorted((-i.score, i.key) for i in found)
    assert expand(py_graph, [Seed(ADD, 1.0)]) == found


def test_only_the_chosen_edge_kinds_are_followed(py_graph: SnapshotGraph) -> None:
    found = expand(py_graph, [Seed(ADD, 1.0)], kinds=[EdgeKind.CALLS])
    assert {item.via for item in found} == {EdgeKind.CALLS}
    assert CART not in {item.key for item in found}
    route_graph = synthetic([("r", "h", 1.0)], kind=EdgeKind.ROUTES_TO)
    assert expand(route_graph, [Seed("h", 1.0)]) == []  # routes_to is not followed


def test_unknown_seeds_and_cycles_are_handled() -> None:
    graph = synthetic([("a", "b", 1.0), ("b", "a", 1.0), ("b", "b", 1.0)])
    found = expand(graph, [Seed("missing", 1.0), Seed("a", 1.0)])
    assert [(item.key, item.hops) for item in found] == [("b", 1)]
    assert expand(graph, []) == []
