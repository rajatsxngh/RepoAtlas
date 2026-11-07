from __future__ import annotations

from repoatlas.graph.model import EdgeKind, Provenance
from repoatlas.parsing.models import Range
from tests.unit.graph.helpers import edge, graph_of, pairs


def test_same_file_and_imported_bases() -> None:
    graph = graph_of(
        {
            "shop/__init__.py": "",
            "shop/base.py": "class Model:\n    pass\n\nclass Timestamped:\n    pass\n",
            "shop/cart.py": (
                "from shop.base import Model\n"
                "import shop.base as base\n\n"
                "class Cart(Model):\n    pass\n\n"
                "class GiftCart(Cart, base.Timestamped):\n    pass\n"
            ),
        }
    )
    same = edge(
        graph, EdgeKind.INHERITS, "py:shop/cart.py:GiftCart:class", "py:shop/cart.py:Cart:class"
    )
    assert same.provenance == Provenance.TREE_SITTER and same.confidence == 0.95
    assert same.site == Range(start_line=7, start_col=15, end_line=7, end_col=19)
    imported = edge(
        graph, EdgeKind.INHERITS, "py:shop/cart.py:Cart:class", "py:shop/base.py:Model:class"
    )
    assert imported.provenance == Provenance.IMPORT_RESOLVER
    dotted = edge(
        graph,
        EdgeKind.INHERITS,
        "py:shop/cart.py:GiftCart:class",
        "py:shop/base.py:Timestamped:class",
    )
    assert dotted.provenance == Provenance.IMPORT_RESOLVER
    assert "unresolved_bases" not in graph.nodes["py:shop/cart.py:GiftCart:class"].meta


def test_unresolved_bases_are_listed_on_the_class() -> None:
    graph = graph_of(
        {
            "models.py": (
                "from pydantic import BaseModel\n"
                "from typing import Generic, TypeVar\n"
                "T = TypeVar('T')\n\n"
                "class Local:\n    pass\n\n"
                "class Item(BaseModel, Generic[T], Local):\n    pass\n\n"
                "class Plain(object):\n    pass\n\n"
                "class Error(Exception):\n    pass\n\n"
                "class Mixed(make_base(), metaclass=Meta):\n    pass\n"
            ),
        }
    )
    item = graph.nodes["py:models.py:Item:class"]
    assert item.meta["unresolved_bases"] == ["BaseModel", "Generic[T]"]
    assert pairs(graph, EdgeKind.INHERITS) == {
        ("py:models.py:Item:class", "py:models.py:Local:class")
    }
    assert "unresolved_bases" not in graph.nodes["py:models.py:Plain:class"].meta
    assert graph.nodes["py:models.py:Error:class"].meta["unresolved_bases"] == ["Exception"]
    assert graph.nodes["py:models.py:Mixed:class"].meta["unresolved_bases"] == ["make_base()"]


def test_typescript_extends_with_generics_and_namespaces() -> None:
    graph = graph_of(
        {
            "src/base.ts": "export class Store<T> {}\nexport class Event {}\n",
            "src/app.tsx": (
                'import * as base from "./base";\n'
                'import { Store } from "./base";\n'
                'import React from "react";\n'
                "interface Named { name: string }\n"
                "export class Cart extends Store<number> implements Named { name = 'c'; }\n"
                "export class Click extends base.Event {}\n"
                "export class View extends React.Component {}\n"
                "export class Mixed extends mixin(Store) {}\n"
            ),
        }
    )
    assert pairs(graph, EdgeKind.INHERITS) == {
        ("ts:src/app.tsx:Cart:class", "ts:src/base.ts:Store:class"),
        ("ts:src/app.tsx:Click:class", "ts:src/base.ts:Event:class"),
    }
    assert "unresolved_bases" not in graph.nodes["ts:src/app.tsx:Cart:class"].meta  # implements
    assert graph.nodes["ts:src/app.tsx:View:class"].meta["unresolved_bases"] == ["React.Component"]
    assert graph.nodes["ts:src/app.tsx:Mixed:class"].meta["unresolved_bases"] == ["mixin(Store)"]


def test_bases_resolve_in_the_enclosing_scope() -> None:
    graph = graph_of(
        {
            "app.py": (
                "class Base:\n    pass\n\n"
                "def factory():\n"
                "    class Inner(Base):\n        pass\n"
                "    class Leaf(Inner):\n        pass\n"
                "    return Leaf\n"
            ),
        }
    )
    assert pairs(graph, EdgeKind.INHERITS) == {
        ("py:app.py:factory.Inner:class", "py:app.py:Base:class"),
        ("py:app.py:factory.Leaf:class", "py:app.py:factory.Inner:class"),
    }


def test_inheritance_cycles_terminate() -> None:
    graph = graph_of(
        {
            "a.py": "from b import B\n\nclass A(B):\n    def run(self):\n        self.go()\n",
            "b.py": "from a import A\n\nclass B(A):\n    pass\n",
        }
    )
    assert pairs(graph, EdgeKind.INHERITS) == {
        ("py:a.py:A:class", "py:b.py:B:class"),
        ("py:b.py:B:class", "py:a.py:A:class"),
    }
    assert [u.reason for u in graph.unresolved] == ["not_found"]
