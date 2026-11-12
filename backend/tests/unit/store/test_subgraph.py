"""Subgraph aggregation (T5) on the fixture graphs: root, nested dir, file, and symbol levels."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from repoatlas.graph.model import CodeGraph, EdgeKind, NodeKind
from repoatlas.store.cache import LEVELS, Level, SnapshotGraph
from tests.unit.graph.helpers import graph_of

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "golden"
Totals = dict[tuple[str, str, str], tuple[int, float, float]]


@cache
def golden(name: str) -> CodeGraph:
    data = json.loads((GOLDEN / f"{name}.graph.json").read_text(encoding="utf-8"))
    return CodeGraph.model_validate(data["graph"])


def naive(graph: CodeGraph, parent: str, level: Level) -> tuple[list[str], Totals]:
    """Aggregation straight from the definition, walking parent keys for every edge."""

    def chain(key: str | None) -> list[str]:
        found = []
        while key is not None:
            found.append(key)
            key = graph.nodes[key].parent_key
        return found

    if level == "file":
        children = [
            key
            for key, node in graph.nodes.items()
            if node.kind == NodeKind.FILE and parent in chain(key)[1:]
        ]
    else:
        children = [key for key, node in graph.nodes.items() if node.parent_key == parent]
    chosen = set(children)

    def owner(key: str) -> str | None:
        return next((k for k in chain(key) if k in chosen), None)

    totals: Totals = {}
    for edge in graph.edges:
        src, dst = owner(edge.src), owner(edge.dst)
        if src is None or dst is None or src == dst:
            continue
        key = (src, dst, edge.kind.value)
        count, low, high = totals.get(key, (0, 1.0, 0.0))
        totals[key] = (count + 1, min(low, edge.confidence), max(high, edge.confidence))
    return sorted(children), totals


def aggregated(snapshot: SnapshotGraph, parent: str, level: Level) -> tuple[list[str], Totals]:
    found = snapshot.aggregate(snapshot.index[parent], level)
    totals = {
        (snapshot.keys[link.src], snapshot.keys[link.dst], link.kind.value): (
            link.count,
            link.min_confidence,
            link.max_confidence,
        )
        for link in found.links
    }
    return [snapshot.keys[child] for child in found.children], totals


@pytest.mark.parametrize("name", ["py_small", "ts_small"])
def test_every_parent_and_level_matches_the_definition(name: str) -> None:
    graph = golden(name)
    snapshot = SnapshotGraph.from_code_graph(graph)
    checked = 0
    for key, node in graph.nodes.items():
        levels: tuple[Level, ...] = ("dir", "file") if node.kind == NodeKind.DIR else ("symbol",)
        for level in levels:
            assert aggregated(snapshot, key, level) == naive(graph, key, level), (key, level)
            checked += 1
    assert checked == len(graph.nodes) + graph.stats.nodes_by_kind["dir"]


def test_root_level() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("py_small"))
    children, totals = aggregated(snapshot, "dir:.", "dir")
    assert children == ["dir:scripts", "dir:src", "dir:stubs", "dir:tests", "dir:vendor"]
    assert totals == {
        ("dir:src", "dir:tests", "tested_by"): (9, 0.145, 0.98),
        ("dir:tests", "dir:src", "imports"): (2, 1.0, 1.0),
        ("dir:tests", "dir:src", "calls"): (8, 0.145, 0.94),
    }


def test_nested_directory_level() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("py_small"))
    children, totals = aggregated(snapshot, "dir:src/shop", "dir")
    assert children == [
        "dir:src/shop/utils",
        "file:src/shop/__init__.py",
        "file:src/shop/app.py",
        "file:src/shop/cart.py",
    ]
    assert totals == {
        ("file:src/shop/__init__.py", "file:src/shop/app.py", "imports"): (1, 1.0, 1.0),
        ("file:src/shop/__init__.py", "file:src/shop/cart.py", "imports"): (1, 1.0, 1.0),
        ("file:src/shop/app.py", "file:src/shop/cart.py", "imports"): (1, 1.0, 1.0),
        ("file:src/shop/app.py", "file:src/shop/cart.py", "calls"): (5, 0.145, 0.94),
        ("file:src/shop/cart.py", "dir:src/shop/utils", "imports"): (1, 1.0, 1.0),
        ("file:src/shop/cart.py", "dir:src/shop/utils", "calls"): (2, 0.94, 0.94),
    }


def test_file_level_flattens_directories() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("py_small"))
    children, totals = aggregated(snapshot, "dir:src", "file")
    assert children == [
        "file:src/shop/__init__.py",
        "file:src/shop/app.py",
        "file:src/shop/cart.py",
        "file:src/shop/utils/__init__.py",
        "file:src/shop/utils/helpers.py",
    ]
    helpers = "file:src/shop/utils/helpers.py"
    assert totals[("file:src/shop/cart.py", helpers, "calls")] == (2, 0.94, 0.94)
    assert totals[("file:src/shop/cart.py", helpers, "imports")] == (1, 1.0, 1.0)
    assert not any(kind == "contains" for _, _, kind in totals)


def test_symbol_level_inside_a_file_and_a_class() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("py_small"))
    children, totals = aggregated(snapshot, "file:src/shop/cart.py", "symbol")
    cart, gift = "py:src/shop/cart.py:Cart:class", "py:src/shop/cart.py:GiftCart:class"
    assert children == [cart, gift]
    assert totals == {
        (gift, cart, "calls"): (1, 0.8, 0.8),
        (gift, cart, "inherits"): (1, 0.95, 0.95),
    }
    methods, inside = aggregated(snapshot, cart, "symbol")
    assert [key.split(":")[2] for key in methods] == [
        "Cart.__init__",
        "Cart._discount",
        "Cart.add",
        "Cart.describe",
        "Cart.total",
    ]
    assert inside == {
        (
            "py:src/shop/cart.py:Cart.describe:method",
            "py:src/shop/cart.py:Cart._discount:method",
            "calls",
        ): (
            1,
            0.99,
            0.99,
        )
    }
    routes, _ = aggregated(snapshot, "file:src/shop/app.py", "symbol")
    assert "route:src/shop/app.py:GET /carts/{cart_id}" in routes


def test_leaves_have_no_children() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("py_small"))
    assert aggregated(snapshot, "py:src/shop/cart.py:Cart.add:method", "symbol") == ([], {})
    assert aggregated(snapshot, "file:vendor/lib.py", "symbol") == ([], {})  # skipped file


def test_invalid_levels_are_rejected() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("py_small"))
    with pytest.raises(ValueError, match="directory"):
        snapshot.aggregate(snapshot.index["file:src/shop/cart.py"], "dir")
    with pytest.raises(ValueError, match="directory"):
        snapshot.aggregate(snapshot.index["py:src/shop/cart.py:Cart:class"], "file")
    with pytest.raises(ValueError, match="not a directory"):
        snapshot.aggregate(snapshot.index["dir:src"], "symbol")
    with pytest.raises(ValueError, match="Unknown level"):
        snapshot.aggregate(snapshot.index["dir:src"], "module")  # type: ignore[arg-type]


def test_results_are_memoized_and_counted_in_the_size() -> None:
    snapshot = SnapshotGraph.from_code_graph(golden("ts_small"))
    before = snapshot.estimated_bytes
    first = snapshot.aggregate(snapshot.index["dir:."], "dir")
    assert snapshot.aggregate(snapshot.index["dir:."], "dir") is first
    assert snapshot.estimated_bytes > before
    assert set(LEVELS) == {"dir", "file", "symbol"}


def test_edges_between_children_count_every_kind() -> None:
    main = "from lib.util import helper\n\ndef run():\n    helper()\n    helper()\n"
    test = "from lib.util import helper\n\ndef test_helper():\n    helper()\n"
    graph = graph_of(
        {
            "app/main.py": main,
            "lib/util.py": "def helper():\n    pass\n",
            "tests/test_util.py": test,
        }
    )
    snapshot = SnapshotGraph.from_code_graph(graph)
    _, totals = aggregated(snapshot, "dir:.", "dir")
    kinds: dict[tuple[str, str], set[str]] = {(src, dst): set() for src, dst, _ in totals}
    for src, dst, kind in totals:
        kinds[(src, dst)].add(kind)
    assert kinds[("dir:app", "dir:lib")] == {"imports", "calls"}
    assert kinds[("dir:lib", "dir:tests")] == {"tested_by"}
    assert totals[("dir:app", "dir:lib", "calls")][0] == 1  # one edge, though called twice
    assert all(EdgeKind(kind) != EdgeKind.CONTAINS for _, _, kind in totals)
