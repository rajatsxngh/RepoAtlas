from __future__ import annotations

from collections import Counter

import pytest

from repoatlas.graph.build import build_graph
from repoatlas.graph.edges import EdgeSet
from repoatlas.graph.keys import ancestors, dir_key, file_key, parent_dir, route_key
from repoatlas.graph.model import EdgeKind, NodeKind, Provenance
from repoatlas.parsing.models import Range
from tests.unit.graph.helpers import graph_of, pairs, parse_files

PY_CART = """\
class Cart:
    def add(self, item):
        def check(value):
            return value
        return check(item)


def total(items):
    return sum(items)
"""

PY_TEST = """\
def test_total():
    assert True


class TestCart:
    def test_add(self):
        pass

    def helper(self):
        pass
"""

TS_TEST = """\
describe("Cart", () => {
  it("adds", () => {});
  describe("empty", () => {
    it("has no total", () => {});
  });
});
"""


def test_keys_for_directories_files_and_routes() -> None:
    assert dir_key(".") == dir_key("") == "dir:."
    assert dir_key("src/pkg") == "dir:src/pkg"
    assert file_key("a:b/c~d%.py") == "file:a%3Ab/c%7Ed%25.py"
    assert route_key("src/server.ts", "GET", "/carts/:id") == "route:src/server.ts:GET /carts/%3Aid"
    assert parent_dir("a.py") == "." and parent_dir("a/b/c.py") == "a/b"
    assert ancestors("a/b/c.py") == [".", "a", "a/b"]
    assert ancestors("c.py") == ["."]


def test_directories_for_every_inventoried_file() -> None:
    graph = graph_of(
        {
            "src/pkg/a.py": "x = 1\n",
            "src/pkg/sub/b.py": "y = 2\n",
            "README.md": "# not source\n",
            "node_modules/lib/index.js": "module.exports = 1;\n",
        }
    )
    dirs = {key for key, node in graph.nodes.items() if node.kind == NodeKind.DIR}
    assert dirs == {
        "dir:.",
        "dir:src",
        "dir:src/pkg",
        "dir:src/pkg/sub",
        "dir:node_modules",
        "dir:node_modules/lib",
    }
    files = {key for key, node in graph.nodes.items() if node.kind == NodeKind.FILE}
    assert files == {"file:src/pkg/a.py", "file:src/pkg/sub/b.py", "file:node_modules/lib/index.js"}
    contains = pairs(graph, EdgeKind.CONTAINS)
    assert ("dir:.", "dir:src") in contains
    assert ("dir:src/pkg", "dir:src/pkg/sub") in contains
    assert ("dir:src/pkg/sub", "file:src/pkg/sub/b.py") in contains
    root = graph.nodes["dir:."]
    assert (root.name, root.path, root.parent_key) == (".", ".", None)
    sub = graph.nodes["dir:src/pkg/sub"]
    assert (sub.name, sub.qualified_name, sub.parent_key) == ("sub", "src/pkg/sub", "dir:src/pkg")


def test_skipped_files_get_file_nodes_but_no_symbols() -> None:
    graph = graph_of(
        {"vendor/lib.py": "def vendored():\n    pass\n", "app.py": "def run():\n    pass\n"}
    )
    vendored = graph.nodes["file:vendor/lib.py"]
    assert vendored.meta["skip_reason"] == "vendored"
    assert not any(node.path == "vendor/lib.py" for node in graph.nodes.values() if node.span)
    app = graph.nodes["file:app.py"]
    assert "skip_reason" not in app.meta
    assert app.meta["language"] == "py" and app.meta["is_test"] is False
    assert app.meta["loc"] == 2 and len(str(app.meta["blob_oid"])) == 40
    assert "py:app.py:run:function" in graph.nodes


def test_python_symbols_nest_under_their_parents() -> None:
    graph = graph_of({"shop/cart.py": PY_CART, "tests/test_cart.py": PY_TEST})
    kinds = {key: node.kind for key, node in graph.nodes.items() if node.span is not None}
    assert kinds == {
        "py:shop/cart.py:Cart:class": NodeKind.CLASS,
        "py:shop/cart.py:Cart.add:method": NodeKind.METHOD,
        "py:shop/cart.py:Cart.add.check:function": NodeKind.FUNCTION,
        "py:shop/cart.py:total:function": NodeKind.FUNCTION,
        "py:tests/test_cart.py:test_total:test": NodeKind.TEST,
        "py:tests/test_cart.py:TestCart:class": NodeKind.CLASS,
        "py:tests/test_cart.py:TestCart.test_add:test": NodeKind.TEST,
        "py:tests/test_cart.py:TestCart.helper:method": NodeKind.METHOD,
    }
    contains = pairs(graph, EdgeKind.CONTAINS)
    assert ("file:shop/cart.py", "py:shop/cart.py:Cart:class") in contains
    assert ("py:shop/cart.py:Cart:class", "py:shop/cart.py:Cart.add:method") in contains
    assert (
        "py:shop/cart.py:Cart.add:method",
        "py:shop/cart.py:Cart.add.check:function",
    ) in contains
    assert ("file:tests/test_cart.py", "py:tests/test_cart.py:test_total:test") in contains
    assert (
        "py:tests/test_cart.py:TestCart:class",
        "py:tests/test_cart.py:TestCart.test_add:test",
    ) in contains

    method = graph.nodes["py:shop/cart.py:Cart.add:method"]
    assert method.qualified_name == "Cart.add" and method.path == "shop/cart.py"
    assert method.name_range == Range(start_line=2, start_col=8, end_line=2, end_col=11)
    assert method.span is not None and method.span.start_line == 2
    assert method.meta["signature"] == "def add(self, item)"
    test = graph.nodes["py:tests/test_cart.py:TestCart.test_add:test"]
    assert test.meta["framework"] == "pytest"
    assert test.meta["runner_id"] == "tests/test_cart.py::TestCart::test_add"


def test_typescript_tests_and_members() -> None:
    graph = graph_of(
        {
            "src/cart.ts": "export class Cart {\n  static empty() { return new Cart(); }\n}\n",
            "src/cart.test.ts": TS_TEST,
        }
    )
    tests = {key for key, node in graph.nodes.items() if node.kind == NodeKind.TEST}
    assert tests == {
        "ts:src/cart.test.ts:Cart > adds:test",
        "ts:src/cart.test.ts:Cart > empty > has no total:test",
    }
    for key in tests:
        assert graph.nodes[key].parent_key == "file:src/cart.test.ts"
    method = graph.nodes["ts:src/cart.ts:Cart.empty:method"]
    assert method.meta["modifiers"] == ["static"]
    assert method.exported is True
    assert graph.nodes["ts:src/cart.ts:Cart:class"].parent_key == "file:src/cart.ts"


def test_contains_edges_form_a_tree() -> None:
    graph = graph_of(
        {
            "shop/cart.py": PY_CART,
            "tests/test_cart.py": PY_TEST,
            "web/src/cart.test.ts": TS_TEST,
            "web/src/deep/er/x.ts": "export function f() { return function g() {}; }\n",
        }
    )
    contains = graph.edges_of(EdgeKind.CONTAINS)
    incoming = Counter(edge.dst for edge in contains)
    for key, node in graph.nodes.items():
        if node.parent_key is None:
            assert key == "dir:."
            assert incoming[key] == 0
        else:
            assert incoming[key] == 1, key
            assert node.parent_key in graph.nodes
    for edge in contains:
        assert edge.provenance == Provenance.TREE_SITTER
        assert edge.confidence == 1.0 and edge.site is None and edge.meta == {}
        assert graph.nodes[edge.dst].parent_key == edge.src
    assert graph.stats.edges_by_kind["contains"] == len(contains)
    assert sum(graph.stats.nodes_by_kind.values()) == len(graph.nodes)


def test_path_characters_are_percent_encoded_in_keys() -> None:
    graph = graph_of({"odd:dir/f~1%.py": "def run():\n    pass\n"})
    assert "dir:odd%3Adir" in graph.nodes
    assert "file:odd%3Adir/f%7E1%25.py" in graph.nodes
    assert graph.nodes["file:odd%3Adir/f%7E1%25.py"].path == "odd:dir/f~1%.py"
    assert graph.nodes["py:odd%3Adir/f%7E1%25.py:run:function"].parent_key == (
        "file:odd%3Adir/f%7E1%25.py"
    )


def test_an_empty_inventory_has_only_the_root() -> None:
    graph = graph_of({"README.md": "nothing to parse\n"})
    assert list(graph.nodes) == ["dir:."]
    assert graph.edges == []


def test_parse_results_must_belong_to_indexed_files() -> None:
    output = parse_files({"app.py": "x = 1\n"})
    other = parse_files({"other.py": "y = 2\n"})
    with pytest.raises(ValueError, match="not an indexed inventory file"):
        build_graph(output.inventory, [*output.files, *other.files])


def test_builds_are_deterministic() -> None:
    files = {"shop/cart.py": PY_CART, "tests/test_cart.py": PY_TEST, "src/x.test.ts": TS_TEST}
    assert graph_of(files).digest() == graph_of(dict(reversed(files.items()))).digest()


def site(line: int) -> Range:
    return Range(start_line=line, start_col=0, end_line=line, end_col=4)


def test_edge_sets_collapse_repeated_evidence() -> None:
    edges = EdgeSet()
    calls, match = EdgeKind.CALLS, Provenance.NAME_MATCH
    edges.add("a", "b", calls, match, 0.5, site=site(9), meta={"tier": "T5"}, counted=True)
    edges.add("a", "b", calls, match, 0.95, site=site(7), meta={"tier": "T1"}, counted=True)
    edges.add("a", "b", calls, match, 0.95, site=site(3), meta={"tier": "T1"}, counted=True)
    edges.add("a", "b", calls, match, 0.95, site=site(5), meta={"tier": "T1"}, counted=True)
    edges.add("a", "b", EdgeKind.TESTED_BY, match, 0.3)
    edges.add("a", "b", EdgeKind.TESTED_BY, match, 0.2)
    assert len(edges) == 2 and ("a", "b", calls, match) in edges
    by_kind = {edge.kind: edge for edge in edges.edges()}
    best = by_kind[calls]
    assert best.confidence == 0.95 and best.site == site(3)
    assert best.meta == {"tier": "T1", "count": 4}
    assert by_kind[EdgeKind.TESTED_BY].confidence == 0.3
    assert by_kind[EdgeKind.TESTED_BY].meta == {}
