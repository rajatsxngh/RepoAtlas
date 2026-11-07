from __future__ import annotations

from collections.abc import Mapping

import pytest

from repoatlas.graph.model import CodeGraph, Edge, EdgeKind, Provenance, UnresolvedCall
from repoatlas.graph.tiers import TIER_TABLE, Tier, tier_confidence
from tests.unit.graph.helpers import graph_of


def calls(graph: CodeGraph) -> dict[tuple[str, str], Edge]:
    return {(e.src, e.dst): e for e in graph.edges if e.kind == EdgeKind.CALLS}


def call(graph: CodeGraph, src: str, dst: str) -> Edge:
    found = calls(graph).get((src, dst))
    assert found is not None, (src, dst, sorted(calls(graph)))
    return found


def tier_of(graph: CodeGraph, src: str, dst: str) -> tuple[str, float]:
    edge = call(graph, src, dst)
    return str(edge.meta["tier"]), edge.confidence


def unresolved(graph: CodeGraph, callee: str) -> UnresolvedCall:
    found = [u for u in graph.unresolved if u.callee_text == callee]
    assert len(found) == 1, (callee, graph.unresolved)
    return found[0]


def build(files: Mapping[str, str]) -> CodeGraph:
    return graph_of(files)


def test_the_tier_table_matches_the_prd() -> None:
    assert [(spec.tier.value, spec.confidence) for spec in TIER_TABLE] == [
        ("T1", 0.95),
        ("T2", 0.90),
        ("T3", 0.85),
        ("T4", 0.80),
        ("T5", 0.50),
        ("T6", 0.30),
    ]
    assert tier_confidence(Tier.T6, 3) == pytest.approx(0.1)
    assert tier_confidence(Tier.T1, 3) == 0.95


def test_t1_names_bound_by_import() -> None:
    graph = build(
        {
            "shop/money.py": "def fmt(v):\n    return v\n",
            "shop/app.py": "from shop.money import fmt as f\n\ndef run():\n    return f(1)\n",
            "web/money.ts": "export function fmt(v: number) { return v; }\n",
            "web/app.ts": 'import { fmt } from "./money";\nexport const run = () => fmt(1);\n',
        }
    )
    edge = call(graph, "py:shop/app.py:run:function", "py:shop/money.py:fmt:function")
    assert (edge.meta, edge.confidence) == ({"tier": "T1", "count": 1}, 0.95)
    assert edge.provenance == Provenance.NAME_MATCH
    assert edge.site is not None and edge.site.start_line == 4
    assert tier_of(graph, "ts:web/app.ts:run:function", "ts:web/money.ts:fmt:function") == (
        "T1",
        0.95,
    )


def test_t1_names_defined_in_the_same_file_scope() -> None:
    graph = build(
        {
            "app.py": (
                "def helper():\n    pass\n\n"
                "def outer():\n"
                "    def inner():\n        return helper()\n"
                "    return inner()\n\n"
                "helper()\n"
            ),
            "ui.tsx": (
                "function Save() { return null; }\n"
                "export function Bar() { return <Save />; }\n"
                "const api = { get() { return 1; } };\n"
                "export function useApi() { return api.get(); }\n"
            ),
        }
    )
    assert tier_of(graph, "py:app.py:outer:function", "py:app.py:outer.inner:function")[0] == "T1"
    assert tier_of(graph, "py:app.py:outer.inner:function", "py:app.py:helper:function")[0] == "T1"
    assert tier_of(graph, "file:app.py", "py:app.py:helper:function")[0] == "T1"  # module level
    assert tier_of(graph, "ts:ui.tsx:Bar:function", "ts:ui.tsx:Save:function")[0] == "T1"  # JSX
    assert tier_of(graph, "ts:ui.tsx:useApi:function", "ts:ui.tsx:api.get:function")[0] == "T1"


def test_t2_members_of_imported_modules_and_classes() -> None:
    graph = build(
        {
            "shop/__init__.py": "",
            "shop/money.py": "def fmt(v):\n    return v\n",
            "shop/cart.py": "class Cart:\n    @classmethod\n    def empty(cls):\n        return 1",
            "shop/app.py": (
                "import shop.money as money\n"
                "import shop.money\n"
                "from shop.cart import Cart\n\n"
                "def run():\n"
                "    money.fmt(1)\n"
                "    shop.money.fmt(2)\n"
                "    return Cart.empty()\n"
            ),
            "web/money.ts": "export function fmt() {}\n",
            "web/legacy.js": "exports.join = function join() {};\n",
            "web/app.ts": (
                'import * as money from "./money";\n'
                'const legacy = require("./legacy");\n'
                "export function run() { money.fmt(); legacy.join(); }\n"
            ),
        }
    )
    run = "py:shop/app.py:run:function"
    assert tier_of(graph, run, "py:shop/money.py:fmt:function") == ("T2", 0.9)
    assert call(graph, run, "py:shop/money.py:fmt:function").meta["count"] == 2
    assert tier_of(graph, run, "py:shop/cart.py:Cart.empty:method") == ("T2", 0.9)
    web = "ts:web/app.ts:run:function"
    assert tier_of(graph, web, "ts:web/money.ts:fmt:function") == ("T2", 0.9)
    assert tier_of(graph, web, "js:web/legacy.js:join:function") == ("T2", 0.9)


def test_t3_self_and_this_on_the_class_and_its_bases() -> None:
    graph = build(
        {
            "base.py": "class Base:\n    def save(self):\n        pass\n",
            "models.py": (
                "from base import Base\n\n"
                "class User(Base):\n"
                "    def check(self):\n        pass\n\n"
                "    def store(self):\n"
                "        self.check()\n"
                "        def later():\n            return self.save()\n"
                "        return later()\n\n"
                "    @classmethod\n"
                "    def make(cls):\n        return cls.check()\n"
            ),
            "model.ts": (
                "class Base { save() {} }\n"
                "export class User extends Base {\n"
                "  check() {}\n"
                "  store() { this.check(); [1].forEach(() => this.save()); }\n"
                "}\n"
                "export const api = { get() { return this.list(); }, list() { return []; } };\n"
            ),
        }
    )
    assert tier_of(graph, "py:models.py:User.store:method", "py:models.py:User.check:method") == (
        "T3",
        0.85,
    )
    later = "py:models.py:User.store.later:function"
    assert tier_of(graph, later, "py:base.py:Base.save:method")[0] == "T3"
    assert tier_of(graph, "py:models.py:User.make:method", "py:models.py:User.check:method")[0] == (
        "T3"
    )
    store = "ts:model.ts:User.store:method"
    assert tier_of(graph, store, "ts:model.ts:User.check:method")[0] == "T3"
    assert tier_of(graph, store, "ts:model.ts:Base.save:method")[0] == "T3"
    assert tier_of(graph, "ts:model.ts:api.get:function", "ts:model.ts:api.list:function")[0] == (
        "T3"
    )


def test_t4_super_calls_reach_the_bases() -> None:
    graph = build(
        {
            "models.py": (
                "class Base:\n"
                "    def __init__(self):\n        pass\n"
                "    def save(self):\n        pass\n\n"
                "class User(Base):\n"
                "    def __init__(self):\n        super().__init__()\n"
                "    def save(self):\n        return super(User, self).save()\n"
            ),
            "model.ts": (
                "class Base { constructor() {} save() {} }\n"
                "class Plain {}\n"
                "class User extends Base { constructor() { super(); } save() { super.save(); } }\n"
                "class Other extends Plain { constructor() { super(); } }\n"
            ),
        }
    )
    assert tier_of(
        graph, "py:models.py:User.__init__:method", "py:models.py:Base.__init__:method"
    ) == (
        "T4",
        0.8,
    )
    assert (
        tier_of(graph, "py:models.py:User.save:method", "py:models.py:Base.save:method")[0] == "T4"
    )
    ctor = "ts:model.ts:User.constructor:method"
    assert tier_of(graph, ctor, "ts:model.ts:Base.constructor:method")[0] == "T4"
    assert tier_of(graph, "ts:model.ts:User.save:method", "ts:model.ts:Base.save:method")[0] == "T4"
    assert tier_of(graph, "ts:model.ts:Other.constructor:method", "ts:model.ts:Plain:class")[0] == (
        "T4"
    )


def test_t5_exactly_one_defining_class() -> None:
    graph = build(
        {
            "cart.py": (
                "class Cart:\n"
                "    @property\n    def total(self):\n        return 0\n"
                "    @total.setter\n    def total(self, value):\n        pass\n"
                "    def describe(self):\n        return ''\n"
            ),
            "app.py": "def show(cart):\n    cart.describe()\n    cart.total()\n",
        }
    )
    edge = call(graph, "py:app.py:show:function", "py:cart.py:Cart.describe:method")
    assert (edge.meta, edge.confidence) == ({"tier": "T5", "candidates": 1, "count": 1}, 0.5)
    # A getter and setter pair is one defining class: still T5, to the later definition.
    assert tier_of(graph, "py:app.py:show:function", "py:cart.py:Cart.total:method~2")[0] == "T5"


def test_t6_two_to_five_candidates_split_the_confidence() -> None:
    owners = {f"m{i}.py": f"class C{i}:\n    def add(self):\n        pass\n" for i in range(3)}
    graph = build({**owners, "app.py": "def run(x):\n    x.add()\n"})
    for i in range(3):
        edge = call(graph, "py:app.py:run:function", f"py:m{i}.py:C{i}.add:method")
        assert edge.meta == {"tier": "T6", "candidates": 3, "count": 1}
        assert edge.confidence == 0.1


def test_more_than_five_candidates_are_unresolved() -> None:
    owners = {f"m{i}.py": f"class C{i}:\n    def get(self):\n        pass\n" for i in range(6)}
    graph = build({**owners, "app.py": "def run(x):\n    return x.get()\n"})
    assert not any(src == "py:app.py:run:function" for src, _ in calls(graph))
    record = unresolved(graph, "x.get")
    assert record.reason == "ambiguous"
    assert record.caller_key == "py:app.py:run:function"
    assert record.receiver_text == "x"
    assert record.candidates == [f"py:m{i}.py:C{i}.get:method" for i in range(6)]
    assert record.site.start_line == 2
    assert graph.stats.unresolved_by_reason == {"ambiguous": 1}


def test_unresolved_calls_keep_their_reason() -> None:
    graph = build(
        {
            "lib.py": "def helper():\n    pass\n",
            "app.py": (
                "def run(x, handlers):\n"
                "    x.nothing_defines_this()\n"
                "    helper()\n"
                "    handlers['k']()\n"
                "    run(x, handlers)()\n"
            ),
        }
    )
    assert unresolved(graph, "x.nothing_defines_this").reason == "not_found"
    unbound = unresolved(graph, "helper")
    assert unbound.reason == "unbound_name"
    assert unbound.candidates == ["py:lib.py:helper:function"]
    assert unresolved(graph, "handlers['k']").reason == "dynamic"
    assert unresolved(graph, "run(x, handlers)").reason == "dynamic"
    assert tier_of(graph, "py:app.py:run:function", "py:app.py:run:function")[0] == "T1"


def test_external_and_builtin_calls_are_dropped_and_counted() -> None:
    graph = build(
        {
            "app.py": (
                "import os\n"
                "from decimal import Decimal\n"
                "import unittest\n\n"
                "def run(items):\n"
                "    os.path.join('a', 'b')\n"
                "    Decimal('1').quantize(Decimal('0.01'))\n"
                "    print(len(items))\n"
                "    ', '.join(items)\n"
                "    return sorted(items)\n\n"
                "class T(unittest.TestCase):\n"
                "    def check(self):\n        self.assertEqual(1, 1)\n"
            ),
            "app.ts": (
                'import { expect } from "vitest";\n'
                'import express from "express";\n'
                "const app = express();\n"
                "console.log(JSON.stringify({}));\n"
                "expect(1).toBe(1);\n"
                "`a${1}`.trim();\n"
                "new Map();\n"
            ),
        }
    )
    assert calls(graph) == {}
    assert graph.unresolved == []
    assert graph.stats.external_calls == 8
    assert graph.stats.builtin_calls == 8
    assert graph.stats.call_sites == 16


def test_constructor_calls_link_the_class_and_its_initializer() -> None:
    graph = build(
        {
            "cart.py": (
                "class Cart:\n    def __init__(self):\n        pass\n\n"
                "class GiftCart(Cart):\n    pass\n\n"
                "class Plain:\n    pass\n"
            ),
            "app.py": (
                "from cart import Cart, GiftCart, Plain\n\n"
                "def run():\n    Cart()\n    GiftCart()\n    Plain()\n"
            ),
            "cart.ts": "export class Cart { constructor() {} }\nexport class Bag {}\n",
            "app.ts": (
                'import { Cart, Bag } from "./cart";\n'
                "export const run = () => [new Cart(), new Bag()];\n"
            ),
        }
    )
    run = "py:app.py:run:function"
    assert tier_of(graph, run, "py:cart.py:Cart:class") == ("T1", 0.95)
    assert tier_of(graph, run, "py:cart.py:Cart.__init__:method") == ("T1", 0.95)
    assert tier_of(graph, run, "py:cart.py:GiftCart:class")[0] == "T1"
    assert call(graph, run, "py:cart.py:Cart.__init__:method").meta["count"] == 2  # inherited
    assert tier_of(graph, run, "py:cart.py:Plain:class")[0] == "T1"
    ts_run = "ts:app.ts:run:function"
    assert {dst for src, dst in calls(graph) if src == ts_run} == {
        "ts:cart.ts:Cart:class",
        "ts:cart.ts:Cart.constructor:method",
        "ts:cart.ts:Bag:class",
    }


def test_repeated_calls_collapse_and_the_best_tier_wins() -> None:
    graph = build(
        {
            "cart.py": "class Cart:\n    def add(self):\n        pass\n",
            "app.py": (
                "from cart import Cart\n\n"
                "def run(x):\n"
                "    x.add()\n"
                "    Cart.add(x)\n"
                "    Cart.add(x)\n"
            ),
        }
    )
    edge = call(graph, "py:app.py:run:function", "py:cart.py:Cart.add:method")
    assert edge.meta == {"tier": "T2", "count": 3}
    assert edge.confidence == 0.9
    assert edge.site is not None and edge.site.start_line == 5  # the first T2 call


def test_calls_only_match_within_a_language_family() -> None:
    graph = build(
        {
            "cart.py": "class Cart:\n    def add(self):\n        pass\n",
            "cart.ts": "export class Cart { add() {} }\n",
            "app.js": "export function run(x) { return x.add(); }\n",
        }
    )
    assert set(calls(graph)) == {("js:app.js:run:function", "ts:cart.ts:Cart.add:method")}


def test_only_instance_members_are_name_matched() -> None:
    # `items.map()` is almost always Array.prototype.map, not a module's exported `map`, and
    # `items.find()` not a namespace's `find`: modules, namespaces, and object literals are
    # reached through imports, which T1 and T2 resolve by binding. Functions assigned onto
    # objects or prototypes are instance methods, the JavaScript way.
    graph = build(
        {
            "a.ts": (
                "export function map() {}\n"
                "export const api = { list() {} };\n"
                "export namespace util { export const find = () => 1; }\n"
            ),
            "router.js": (
                "function Router() {}\n"
                "Router.prototype.route = function route(path) {};\n"
                "res.json = function json(body) {};\n"
            ),
            "b.ts": "export function run(x) { x.map(); x.list(); x.find(); x.route(); x.json(); }",
            "util.py": "def helper():\n    pass\n",
            "app.py": "def run(x):\n    x.helper()\n",
        }
    )
    assert set(calls(graph)) == {
        ("ts:b.ts:run:function", "js:router.js:Router.route:function"),
        ("ts:b.ts:run:function", "js:router.js:res.json:function"),
    }
    for callee in ("x.map", "x.list", "x.find", "x.helper"):
        assert unresolved(graph, callee).reason == "not_found"


def test_namespace_and_object_members_resolve_through_bindings() -> None:
    graph = build(
        {
            "a.ts": (
                "export const api = { list() {} };\n"
                "export namespace util { export const find = () => 1; }\n"
            ),
            "b.ts": (
                'import { api, util } from "./a";\n'
                "export function run() { api.list(); util.find(); }\n"
            ),
        }
    )
    run = "ts:b.ts:run:function"
    assert tier_of(graph, run, "ts:a.ts:api.list:function")[0] == "T2"
    assert tier_of(graph, run, "ts:a.ts:util.find:function")[0] == "T2"


def test_production_code_never_matches_members_defined_in_tests() -> None:
    graph = build(
        {
            "src/ctx.py": "def pop(resource):\n    resource.close()\n",
            "tests/test_other.py": "def test_other(other):\n    other.close()\n",
            "tests/test_ctx.py": (
                "def test_pop():\n"
                "    class Wrapper:\n"
                "        def close(self):\n            pass\n"
                "    Wrapper().close()\n"
            ),
        }
    )
    assert unresolved(graph, "resource.close").reason == "not_found"
    # A class defined inside a function is reachable from its own file only.
    assert unresolved(graph, "other.close").reason == "not_found"
    test = "py:tests/test_ctx.py:test_pop:test"
    assert tier_of(graph, test, "py:tests/test_ctx.py:test_pop.Wrapper.close:method")[0] == "T5"


def test_call_statistics_add_up() -> None:
    graph = build(
        {
            "cart.py": "class Cart:\n    def add(self):\n        self.add()\n",
            "app.py": "import os\n\ndef run(x):\n    x.add()\n    os.getcwd()\n    len(x)\n    y()",
        }
    )
    stats = graph.stats
    assert stats.call_sites_by_tier == {"T3": 1, "T5": 1}
    assert stats.unresolved_by_reason == {"unbound_name": 1}
    assert (stats.external_calls, stats.builtin_calls, stats.unresolved_calls) == (1, 1, 1)
    assert stats.call_sites == 5
    assert stats.edges_by_kind["calls"] == 2
