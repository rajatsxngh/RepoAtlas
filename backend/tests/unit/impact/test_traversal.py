"""Reverse traversal and scoring (T1): decay * confidence, noisy-or, cycles, depth cap."""

from __future__ import annotations

import pytest

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.impact.traversal import OVERRIDES, Propagation, propagate
from repoatlas.store.cache import SnapshotGraph
from tests.unit.impact.helpers import fn, synthetic

CALLS = EdgeKind.CALLS


def run(graph: SnapshotGraph, *starts: str, **params: object) -> Propagation:
    options: dict[str, object] = {"max_depth": 4, "decay": 0.5, "min_score": 0.0}
    options.update(params)
    return propagate(graph, [graph.index[s] for s in starts], **options)  # type: ignore[arg-type]


def scores(graph: SnapshotGraph, result: Propagation) -> dict[str, float]:
    return {graph.keys[r.node]: round(r.score, 6) for r in result.items()}


def test_a_chain_decays_by_decay_times_confidence_per_hop() -> None:
    # c calls b calls a: changing a reaches b, then c
    graph = synthetic([("b", "a", CALLS, 0.8), ("c", "b", CALLS, 0.5)])
    result = run(graph, "a")
    assert scores(graph, result) == {"b": 0.4, "c": 0.1}
    depths = {graph.keys[r.node]: r.depth for r in result.items()}
    assert depths == {"b": 1, "c": 2}
    assert [(graph.keys[s.src], graph.keys[s.dst], s.kind) for s in result.path_to(2)] == [
        ("a", "b", "calls"),
        ("b", "c", "calls"),
    ]


def test_a_diamond_combines_its_two_paths_with_noisy_or() -> None:
    # d calls b and c; both call a
    graph = synthetic([("b", "a"), ("c", "a"), ("d", "b"), ("d", "c")], confidence=1.0)
    result = run(graph, "a")
    got = scores(graph, result)
    assert got["b"] == got["c"] == 0.5
    # two paths of 0.25 each: 1 - 0.75 * 0.75
    assert got["d"] == pytest.approx(0.4375)
    best = {graph.keys[r.node]: r.best for r in result.items()}
    assert best["d"] == pytest.approx(0.25)


def test_parallel_edges_from_one_parent_count_once_at_their_best() -> None:
    graph = synthetic([("b", "a", CALLS, 0.4), ("b", "a", CALLS, 0.8)])
    assert scores(graph, run(graph, "a")) == {"b": 0.4}


def test_the_best_path_is_the_highest_scoring_one_not_the_shortest() -> None:
    # x reaches a directly with a weak edge, and through b with strong ones
    graph = synthetic([("x", "a", CALLS, 0.1), ("b", "a", CALLS, 1.0), ("x", "b", CALLS, 1.0)])
    result = run(graph, "a")
    x = result.reached[graph.index["x"]]
    assert x.best == pytest.approx(0.25)
    assert x.depth == 2
    assert graph.keys[x.via.src] == "b"  # type: ignore[union-attr]
    assert x.score == pytest.approx(1 - (1 - 0.05) * (1 - 0.25))


@pytest.mark.parametrize("shape", ["self", "mutual", "long"])
def test_cycles_end_and_never_reach_the_start(shape: str) -> None:
    edges = {
        "self": [("b", "a"), ("b", "b")],
        "mutual": [("b", "a"), ("c", "b"), ("b", "c"), ("a", "c")],
        "long": [("b", "a"), ("c", "b"), ("d", "c"), ("b", "d"), ("a", "d")],
    }[shape]
    graph = synthetic(edges)
    result = run(graph, "a", max_depth=50, decay=1.0)
    assert "a" not in scores(graph, result)
    assert all(0 < value <= 1 for value in scores(graph, result).values())
    assert all(r.expanded for r in result.reached.values())


def test_the_depth_cap_stops_expansion_but_keeps_the_last_level() -> None:
    graph = synthetic([("b", "a"), ("c", "b"), ("d", "c"), ("e", "d")])
    assert set(scores(graph, run(graph, "a", max_depth=2, decay=1.0))) == {"b", "c"}
    assert set(scores(graph, run(graph, "a", max_depth=0, decay=1.0))) == set()
    assert set(scores(graph, run(graph, "a", max_depth=4, decay=1.0))) == {"b", "c", "d", "e"}


def test_nodes_below_min_score_are_reached_but_not_expanded() -> None:
    graph = synthetic([("b", "a", CALLS, 0.2), ("c", "b", CALLS, 1.0)])
    result = run(graph, "a", decay=0.5, min_score=0.15)
    assert scores(graph, result) == {"b": 0.1}  # b is listed; c is never reached
    assert result.expanded == 1


def test_edge_directions_per_kind() -> None:
    kinds = {"Base": NodeKind.CLASS, "Sub": NodeKind.CLASS, "route": NodeKind.ROUTE}
    graph = synthetic(
        [
            ("Sub", "Base", EdgeKind.INHERITS),  # subclass → base: changing Base hits Sub
            ("Base", "test_base", EdgeKind.TESTED_BY),  # production → test
            ("route", "Base", EdgeKind.ROUTES_TO),  # route → handler
            ("Base", "helper", CALLS),  # Base calls helper: helper is not affected
            ("importer", "Base", EdgeKind.IMPORTS),
        ],
        kinds=kinds,
    )
    result = run(graph, "Base", max_depth=1)
    assert set(scores(graph, result)) == {"Sub", "test_base", "route", "importer"}
    only_calls = run(graph, "Base", max_depth=1, edge_kinds=[CALLS])
    assert scores(graph, only_calls) == {}


def test_a_start_mapping_restricts_the_first_expansion() -> None:
    graph = synthetic([("b", "a", CALLS), ("t", "a", EdgeKind.IMPORTS)])
    result = propagate(
        graph,
        {graph.index["a"]: [EdgeKind.IMPORTS]},
        max_depth=3,
        decay=0.5,
        min_score=0.0,
    )
    assert set(scores(graph, result)) == {"t"}


def test_several_starts_never_list_each_other() -> None:
    graph = synthetic([("b", "a"), ("c", "b"), ("c", "a")])
    assert set(scores(graph, run(graph, "a", "b"))) == {"c"}
    result = run(graph, "a", "b")
    c = result.reached[graph.index["c"]]
    assert c.score == pytest.approx(0.75)  # one hop from each start


def test_methods_reach_their_overrides_in_subclasses() -> None:
    base, sub, subsub = fn("Base", "class"), fn("Sub", "class"), fn("SubSub", "class")
    run_base = fn("Base.run", "method")
    run_subsub = fn("SubSub.run", "method")
    other_sub = fn("Sub.other", "method")
    caller = fn("main")
    graph = synthetic(
        [
            (sub, base, EdgeKind.INHERITS, 0.9),
            (subsub, sub, EdgeKind.INHERITS, 0.5),
            (caller, run_subsub, CALLS, 1.0),
        ],
        kinds={
            base: NodeKind.CLASS,
            sub: NodeKind.CLASS,
            subsub: NodeKind.CLASS,
            run_base: NodeKind.METHOD,
            run_subsub: NodeKind.METHOD,
            other_sub: NodeKind.METHOD,
        },
        parents={run_base: base, run_subsub: subsub, other_sub: sub},
    )
    result = run(graph, run_base, decay=1.0)
    got = scores(graph, result)
    assert got[run_subsub] == pytest.approx(0.45)  # 0.9 * 0.5 down the chain
    assert got[caller] == pytest.approx(0.45)
    assert other_sub not in got
    step = result.reached[graph.index[run_subsub]].via
    assert step is not None and step.kind == OVERRIDES and step.edge is None
    assert scores(graph, run(graph, run_base, edge_kinds=[CALLS])) == {}


def test_results_do_not_depend_on_input_order() -> None:
    edges = [("b", "a"), ("c", "a"), ("d", "b"), ("d", "c"), ("e", "d"), ("b", "e")]
    first = scores(g1 := synthetic(edges), run(g1, "a"))
    second = scores(g2 := synthetic(list(reversed(edges))), run(g2, "a"))
    assert first == second


@pytest.mark.parametrize(
    "params",
    [{"max_depth": -1}, {"decay": 0.0}, {"decay": 1.5}, {"min_score": 2.0}],
)
def test_bad_parameters_are_rejected(params: dict[str, float]) -> None:
    graph = synthetic([("b", "a")])
    with pytest.raises(ValueError):
        run(graph, "a", **params)
