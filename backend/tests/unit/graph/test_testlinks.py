from __future__ import annotations

import pytest

from repoatlas.graph import testlinks
from repoatlas.graph.model import CodeGraph, Edge, EdgeKind, Provenance
from repoatlas.parsing.models import Range
from tests.unit.graph.helpers import graph_of


def links(graph: CodeGraph, provenance: Provenance) -> dict[tuple[str, str], Edge]:
    return {
        (e.src, e.dst): e
        for e in graph.edges
        if e.kind == EdgeKind.TESTED_BY and e.provenance == provenance
    }


SHOP = {
    "src/shop/__init__.py": "from shop.cart import Cart\n",
    "src/shop/cart.py": (
        "class Cart:\n    def add(self, item):\n        pass\n\ndef total(items):\n    return 0\n"
    ),
    "src/shop/app.py": "from shop.cart import total\n",
    "tests/helpers.py": "def make():\n    pass\n",
    "tests/conftest.py": "from tests.helpers import make\n",
    "tests/test_cart.py": (
        "from shop import Cart\n"
        "from shop.cart import total\n"
        "from tests.helpers import make\n\n"
        "total([])\n\n"
        "def test_add():\n"
        "    def build():\n        return Cart()\n"
        "    cart = build()\n"
        "    cart.add(1)\n"
        "    make()\n\n"
        "class TestTotal:\n"
        "    def test_empty(self):\n        assert total([]) == 0\n"
    ),
}


def test_test_files_importing_production_files() -> None:
    graph = graph_of(SHOP)
    found = links(graph, Provenance.IMPORT_RESOLVER)
    assert set(found) == {
        ("file:src/shop/__init__.py", "file:tests/test_cart.py"),
        ("file:src/shop/cart.py", "file:tests/test_cart.py"),
    }
    cart = found[("file:src/shop/cart.py", "file:tests/test_cart.py")]
    assert cart.confidence == 0.9
    # Two statements link cart.py (one through the package's re-export); the first one wins.
    assert cart.site == Range(start_line=1, start_col=0, end_line=1, end_col=21)


def test_tests_calling_production_symbols() -> None:
    graph = graph_of(SHOP)
    found = links(graph, Provenance.NAME_MATCH)
    add_test = "py:tests/test_cart.py:test_add:test"
    empty_test = "py:tests/test_cart.py:TestTotal.test_empty:test"
    assert set(found) == {
        ("py:src/shop/cart.py:Cart:class", add_test),  # called from a function nested in the test
        ("py:src/shop/cart.py:Cart.add:method", add_test),
        ("py:src/shop/cart.py:total:function", empty_test),
    }
    assert found[("py:src/shop/cart.py:Cart.add:method", add_test)].confidence == 0.5  # T5
    total = found[("py:src/shop/cart.py:total:function", empty_test)]
    assert total.confidence == 0.95
    assert total.site is not None and total.site.start_line == 16


def test_typescript_tests_link_by_import_call_and_name() -> None:
    graph = graph_of(
        {
            "src/cart.ts": "export class Cart { add() {} }\n",
            "src/__tests__/cart.test.ts": (
                'import { Cart } from "../cart";\n'
                'describe("Cart", () => {\n'
                '  it("adds", () => { new Cart().add(); });\n'
                "});\n"
            ),
        }
    )
    test_file, test = (
        "file:src/__tests__/cart.test.ts",
        "ts:src/__tests__/cart.test.ts:Cart > adds:test",
    )
    assert set(links(graph, Provenance.IMPORT_RESOLVER)) == {("file:src/cart.ts", test_file)}
    assert set(links(graph, Provenance.NAME_MATCH)) == {
        ("ts:src/cart.ts:Cart:class", test),
        ("ts:src/cart.ts:Cart.add:method", test),
    }
    convention = links(graph, Provenance.CONVENTION)
    assert set(convention) == {("file:src/cart.ts", test_file)}
    edge = convention[("file:src/cart.ts", test_file)]
    assert (edge.confidence, edge.site) == (0.6, None)


@pytest.mark.parametrize(
    ("path", "stem"),
    [
        ("tests/test_cart.py", "cart"),
        ("pkg/cart_test.py", "cart"),
        ("tests/conftest.py", None),
        ("tests/helpers.py", None),
        ("tests/test_.py", None),
        ("src/cart.test.ts", "cart"),
        ("src/cart.spec.tsx", "cart"),
        ("src/__tests__/cart.ts", "cart"),
        ("src/__tests__/cart.test.js", "cart"),
        ("test/helpers.ts", None),
    ],
)
def test_tested_stem(path: str, stem: str | None) -> None:
    assert testlinks.tested_stem(path) == stem


STEMS = {
    ("py", "cart"): ["src/shop/cart.py"],
    ("py", "util"): ["src/a/util.py", "src/b/util.py"],
    ("py", "models"): ["app/models.py", "lib/models.py"],
    ("js", "cart"): ["src/cart.ts"],
    ("js", "Button"): ["src/components/Button.tsx", "src/legacy/Button.jsx"],
}


@pytest.mark.parametrize(
    ("test", "expected"),
    [
        ("tests/test_cart.py", "src/shop/cart.py"),  # the only candidate
        ("tests/a/test_util.py", "src/a/util.py"),  # the best folder match
        ("tests/test_util.py", None),  # a tie
        ("lib/models_test.py", "lib/models.py"),  # same folder
        ("tests/test_cart.ts", None),  # not a test name in TypeScript
        ("src/__tests__/cart.test.ts", "src/cart.ts"),  # the folder above __tests__
        ("src/components/Button.test.tsx", "src/components/Button.tsx"),
        ("test/legacy/Button.spec.js", "src/legacy/Button.jsx"),
        ("tests/test_missing.py", None),
    ],
)
def test_convention_targets(test: str, expected: str | None) -> None:
    assert testlinks.convention_target(test, STEMS) == expected


def test_convention_links_only_parsed_production_files() -> None:
    graph = graph_of(
        {
            "vendor/cart.py": "x = 1\n",
            "tests/test_cart.py": "def test_x():\n    pass\n",
            "tests/cart.py": "y = 2\n",  # a test helper, not production code
        }
    )
    assert links(graph, Provenance.CONVENTION) == {}
