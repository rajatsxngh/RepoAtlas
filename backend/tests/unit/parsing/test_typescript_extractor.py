from __future__ import annotations

from textwrap import dedent

from repoatlas.parsing.models import ImportBinding, ParsedFile, Symbol
from tests.unit.parsing.helpers import keys, parse_source, symbol

TS = "src/mod.ts"


def ts(code: str, path: str = TS, *, is_test: bool | None = None) -> ParsedFile:
    return parse_source(dedent(code).lstrip("\n"), path, is_test=is_test)


def exported(parsed: ParsedFile) -> dict[str, bool]:
    return {s.qualname: s.exported for s in parsed.symbols}


# --- functions -----------------------------------------------------------------------------------


def test_exported_function_declaration() -> None:
    parsed = ts(
        "export async function load(id: string): Promise<User> {\n  return fetchUser(id);\n}\n"
    )
    fn = symbol(parsed, "load")
    assert fn.key == "ts:src/mod.ts:load:function"
    assert fn.exported is True
    assert fn.modifiers == ["async"]
    assert fn.signature == "async function load(id: string): Promise<User>"
    assert fn.span.start_col == 0  # the span starts at `export`
    assert [e.name for e in parsed.exports] == ["load"]


def test_export_clause_marks_local_functions() -> None:
    parsed = ts("function a() {}\nfunction b() {}\nfunction c() {}\nexport { a, b as bee };\n")
    assert exported(parsed) == {"a": True, "b": True, "c": False}
    assert [(e.name, e.local) for e in parsed.exports] == [("a", "a"), ("bee", "b")]


def test_generator_functions() -> None:
    parsed = ts("function* ids() { yield 1; }\nasync function* stream() {}\n")
    assert symbol(parsed, "ids").modifiers == ["generator"]
    assert symbol(parsed, "stream").modifiers == ["async", "generator"]


def test_arrow_and_function_expressions_bound_to_names() -> None:
    parsed = ts(
        "export const sum = (a: number, b: number): number => a + b;\nlet f = function () {};\n"
    )
    sum_fn = symbol(parsed, "sum")
    assert sum_fn.kind == "function"
    assert sum_fn.signature == "sum = (a: number, b: number): number =>"
    assert sum_fn.exported is True
    assert symbol(parsed, "f").exported is False


def test_nested_functions_are_scoped() -> None:
    parsed = ts("function outer() {\n  function inner() {}\n  const arrow = () => inner();\n}\n")
    assert keys(parsed) == [
        "ts:src/mod.ts:outer:function",
        "ts:src/mod.ts:outer.inner:function",
        "ts:src/mod.ts:outer.arrow:function",
    ]
    assert parsed.calls[0].enclosing == "ts:src/mod.ts:outer.arrow:function"


def test_overload_signatures_are_not_symbols() -> None:
    parsed = ts(
        "function f(a: string): void;\nfunction f(a: number): void;\nfunction f(a: any) {}\n"
    )
    assert keys(parsed) == ["ts:src/mod.ts:f:function"]


def test_types_interfaces_and_enums_are_not_symbols() -> None:
    parsed = ts(
        "interface I { x(): void }\n"
        "type T = { y: number };\n"
        "enum E { A }\n"
        "declare function ext(): void;\n"
    )
    assert parsed.symbols == []


def test_namespaces_prefix_qualnames() -> None:
    parsed = ts("namespace Shapes {\n  export function area() {}\n}\n")
    fn = symbol(parsed, "Shapes.area")
    assert fn.parent is None
    assert fn.key == "ts:src/mod.ts:Shapes.area:function"


# --- classes -------------------------------------------------------------------------------------


def test_class_heritage() -> None:
    parsed = ts("export class Cart extends Base<Item> implements Iterable<Item>, Sized {}\n")
    assert [(b.text, b.relation) for b in parsed.class_bases] == [
        ("Base", "extends"),
        ("Iterable<Item>", "implements"),
        ("Sized", "implements"),
    ]
    assert symbol(parsed, "Cart").signature == (
        "class Cart extends Base<Item> implements Iterable<Item>, Sized"
    )


def test_javascript_class_heritage_expression() -> None:
    parsed = ts("class A extends mixin(B) {}\n", path="lib/a.js")
    assert parsed.class_bases[0].text == "mixin(B)"
    assert symbol(parsed, "A").key == "js:lib/a.js:A:class"
    assert parsed.calls[0].name == "mixin"


def test_methods_and_modifiers() -> None:
    parsed = ts("""
        export class Cart {
          constructor(private repo: Repo) {}
          static async *batches() {}
          get total(): number { return 0; }
          set total(value: number) {}
          #secret() {}
          private hidden() {}
          handle = async (event: Event) => this.add(event);
        }
    """)
    first_by_name: dict[str, list[str]] = {}
    for s in parsed.symbols:
        if s.kind == "method":
            first_by_name.setdefault(s.qualname, s.modifiers)
    assert first_by_name == {
        "Cart.constructor": [],
        "Cart.batches": ["static", "async", "generator"],
        "Cart.total": ["getter"],
        "Cart.#secret": [],
        "Cart.hidden": ["private"],
        "Cart.handle": ["field", "async"],
    }
    assert symbol(parsed, "Cart.total", 2).modifiers == ["setter"]
    assert symbol(parsed, "Cart.total", 2).key == "ts:src/mod.ts:Cart.total:method~2"
    assert exported(parsed)["Cart.batches"] is True
    assert exported(parsed)["Cart.#secret"] is False
    assert exported(parsed)["Cart.hidden"] is False
    handle_call = next(c for c in parsed.calls if c.name == "add")
    assert handle_call.enclosing == "ts:src/mod.ts:Cart.handle:method"


def test_decorators_on_exported_classes_and_methods() -> None:
    parsed = ts("""
        @Component({ selector: "app" })
        export class AppComponent {
          @Input() name = "";
          @HostListener("click")
          onClick() { track(); }
        }
    """)
    cls = symbol(parsed, "AppComponent")
    assert cls.decorators == ['Component({ selector: "app" })']
    assert cls.span.start_line == 1
    assert symbol(parsed, "AppComponent.onClick").decorators == ['HostListener("click")']
    enclosing = {c.name: c.enclosing for c in parsed.calls}
    assert enclosing["Component"] is None
    assert enclosing["HostListener"] == "ts:src/mod.ts:AppComponent:class"
    assert enclosing["track"] == "ts:src/mod.ts:AppComponent.onClick:method"


def test_abstract_classes_skip_abstract_members() -> None:
    parsed = ts("abstract class Shape {\n  abstract area(): number;\n  describe() {}\n}\n")
    assert keys(parsed) == ["ts:src/mod.ts:Shape:class", "ts:src/mod.ts:Shape.describe:method"]
    assert symbol(parsed, "Shape").modifiers == ["abstract"]


def test_computed_and_string_method_names() -> None:
    parsed = ts('class C {\n  [Symbol.iterator]() {}\n  "quoted name"() {}\n}\n')
    assert [s.name for s in parsed.symbols if s.kind == "method"] == [
        "[Symbol.iterator]",
        "quoted name",
    ]


# --- exports and module patterns -----------------------------------------------------------------


def columns(sym: Symbol) -> tuple[int, int, int]:
    r = sym.name_range
    assert r.start_line == r.end_line
    return (r.start_line, r.start_col, r.end_col)


def test_default_exports() -> None:
    anonymous = ts("export default function () { run(); }\n")
    fn = symbol(anonymous, "default")
    assert fn.exported is True
    assert fn.signature == "function ()"
    assert columns(fn) == (1, 7, 14)  # anonymous: named by the `default` keyword
    assert anonymous.calls[0].enclosing == "ts:src/mod.ts:default:function"

    named = ts("export default function main() {}\n")
    assert symbol(named, "main").exported is True
    assert [(e.name, e.local) for e in named.exports] == [("default", "main")]

    by_name = ts("function app() {}\nexport default app;\n")
    assert symbol(by_name, "app").exported is True

    klass = ts("export default class { m() {} }\n")
    assert keys(klass) == ["ts:src/mod.ts:default:class", "ts:src/mod.ts:default.m:method"]
    assert columns(symbol(klass, "default")) == (1, 7, 14)


def test_object_literal_methods_at_module_scope() -> None:
    parsed = ts(
        "export const api = {\n"
        "  get(url) { return http(url); },\n"
        "  post: (url) => http(url),\n"
        "  version: 1,\n"
        "};\n"
    )
    assert {s.qualname: s.exported for s in parsed.symbols} == {"api.get": True, "api.post": True}
    assert {c.enclosing for c in parsed.calls} == {
        "ts:src/mod.ts:api.get:function",
        "ts:src/mod.ts:api.post:function",
    }


def test_wrapped_components_become_symbols() -> None:
    parsed = ts(
        "export const Button = memo(function Button(props) { return render(props); });\n"
        "const Field = forwardRef((props, ref) => use(ref));\n",
        path="src/ui.tsx",
    )
    assert symbol(parsed, "Button").modifiers == ["wrapped"]
    assert symbol(parsed, "Field").exported is False
    enclosing = {c.name: c.enclosing for c in parsed.calls}
    assert enclosing["memo"] is None
    assert enclosing["render"] == "ts:src/ui.tsx:Button:function"
    assert enclosing["use"] == "ts:src/ui.tsx:Field:function"


def test_commonjs_patterns() -> None:
    parsed = ts(
        """
        const path = require("path");
        const { join, resolve: res } = require("node:path");
        exports.join = function join(a, b) { return path.join(a, b); };
        Router.prototype.route = function route(p) {};
        app.init = function () {};
        module.exports = { join, other: helper };
        """,
        path="lib/legacy.js",
    )
    assert {s.qualname: s.exported for s in parsed.symbols} == {
        "join": True,
        "Router.route": False,
        "app.init": False,
    }
    # Name ranges cover the assigned property; signatures still start at the assignment target.
    assert {s.qualname: columns(s) for s in parsed.symbols} == {
        "join": (3, 8, 12),
        "Router.route": (4, 17, 22),
        "app.init": (5, 4, 8),
    }
    assert symbol(parsed, "Router.route").signature == "Router.prototype.route = function route(p)"
    assert symbol(parsed, "join").signature == "exports.join = function join(a, b)"
    first, second = parsed.imports
    assert (first.kind, first.module, first.bindings) == (
        "require",
        "path",
        [ImportBinding(kind="module", name=None, local="path")],
    )
    assert second.bindings == [
        ImportBinding(kind="named", name="join", local="join"),
        ImportBinding(kind="named", name="resolve", local="res"),
    ]
    assert ("other", "helper") in [(e.name, e.local) for e in parsed.exports]


def test_module_exports_identifier_is_the_default_export() -> None:
    parsed = ts("function createApp() {}\nmodule.exports = createApp;\n", path="lib/app.js")
    assert symbol(parsed, "createApp").exported is True


# --- imports ------------------------------------------------------------------------------------


def test_import_forms() -> None:
    parsed = ts(
        'import React, * as all from "react";\n'
        'import { a as b, type C } from "./b";\n'
        'import type { D } from "./d";\n'
        'import "./side-effect.css";\n'
    )
    react, named, types, side = parsed.imports
    assert react.bindings == [
        ImportBinding(kind="default", name="default", local="React"),
        ImportBinding(kind="namespace", name=None, local="all"),
    ]
    assert named.bindings == [
        ImportBinding(kind="named", name="a", local="b"),
        ImportBinding(kind="named", name="C", local="C"),
    ]
    assert (types.type_only, named.type_only) == (True, False)
    assert (side.module, side.bindings) == ("./side-effect.css", [])


def test_reexports() -> None:
    parsed = ts(
        'export * from "./a";\nexport * as b from "./b";\nexport { c as d, e } from "./c";\n'
    )
    assert [(i.kind, i.module, [x.kind for x in i.bindings]) for i in parsed.imports] == [
        ("reexport", "./a", ["star"]),
        ("reexport", "./b", ["namespace"]),
        ("reexport", "./c", ["named", "named"]),
    ]
    assert [e.name for e in parsed.exports] == ["b", "d", "e"]


def test_dynamic_imports() -> None:
    parsed = ts('const load = () => import("./heavy");\n')
    (dynamic,) = parsed.imports
    assert (dynamic.kind, dynamic.module, dynamic.scope) == (
        "dynamic",
        "./heavy",
        "ts:src/mod.ts:load:function",
    )
    assert parsed.calls == []


# --- calls, JSX, routes --------------------------------------------------------------------------


def test_call_shapes() -> None:
    parsed = ts("""
        class Child extends Base {
          constructor() { super(); }
          run() {
            this.repo.find(1);
            maybe?.method?.();
            new Service.Client(1);
            factory()();
          }
        }
    """)
    shapes = [(c.callee, c.name, c.receiver, c.kind) for c in parsed.calls]
    assert ("super", "super", None, "call") in shapes
    assert ("this.repo.find", "find", "this.repo", "call") in shapes
    assert ("maybe.method", "method", "maybe", "call") in shapes
    assert ("Service.Client", "Client", "Service", "new") in shapes
    assert ("factory()", None, None, "call") in shapes


def test_callbacks_belong_to_the_enclosing_function() -> None:
    parsed = ts("function main() {\n  items.forEach((item) => save(item));\n}\nboot();\n")
    enclosing = {c.name: c.enclosing for c in parsed.calls}
    assert enclosing == {
        "forEach": "ts:src/mod.ts:main:function",
        "save": "ts:src/mod.ts:main:function",
        "boot": None,
    }


def test_jsx_component_usage() -> None:
    parsed = ts(
        "export function Page() {\n"
        "  return <Layout><UI.Button onClick={() => go()} /><div /></Layout>;\n"
        "}\n",
        path="src/page.tsx",
    )
    jsx = [(c.callee, c.name, c.receiver) for c in parsed.calls if c.kind == "jsx"]
    assert jsx == [("Layout", "Layout", None), ("UI.Button", "Button", "UI")]
    assert (
        next(c for c in parsed.calls if c.name == "go").enclosing == "ts:src/page.tsx:Page:function"
    )


def test_express_route_candidates() -> None:
    parsed = ts(
        """
        app.get("/users/:id", auth, getUser);
        router.post("/orders", async (req, res) => res.send());
        router.all("*", fallback);
        cache.get("/not-a-route");
        """,
        path="src/server.js",
    )
    assert [(r.methods, r.path, r.handler) for r in parsed.routes] == [
        (["GET"], "/users/:id", "getUser"),
        (["POST"], "/orders", None),
        (["ALL"], "*", "fallback"),
    ]
    assert parsed.routes[0].text == 'app.get("/users/:id")'


# --- tests --------------------------------------------------------------------------------------


def test_describe_and_it_build_test_symbols() -> None:
    parsed = ts(
        """
        import { Cart } from "../cart";
        describe("Cart", () => {
          beforeEach(() => reset());
          it("adds items", () => {
            new Cart().add(1);
          });
          it("adds items", () => {});
          describe("totals", () => {
            test.only(`sums ${"x"}`, async () => { expect(total()).toBe(0); });
          });
        });
        """,
        path="src/__tests__/cart.test.ts",
    )
    assert [(s.qualname, s.kind) for s in parsed.symbols] == [
        ("Cart > adds items", "test"),
        ("Cart > adds items", "test"),
        ('Cart > totals > sums ${"x"}', "test"),
    ]
    assert parsed.symbols[1].key.endswith("~2")
    assert parsed.tests[0].runner_id == "src/__tests__/cart.test.ts > Cart > adds items"
    assert parsed.tests[2].title_path == ["Cart", "totals", 'sums ${"x"}']
    enclosing = {c.name: c.enclosing for c in parsed.calls}
    assert enclosing["reset"] is None
    assert enclosing["add"] == parsed.symbols[0].key
    assert enclosing["total"] == parsed.symbols[2].key


def test_describe_outside_test_files_is_an_ordinary_call() -> None:
    parsed = ts('describe("x", () => {});\n', path="src/docs.ts")
    assert parsed.symbols == []
    assert parsed.calls[0].name == "describe"


# --- robustness -----------------------------------------------------------------------------------


def test_syntax_errors_are_recorded_and_extraction_continues() -> None:
    parsed = ts("function broken( {\n}\nexport function fine() { return ok(); }\n")
    assert parsed.issue_count >= 1
    assert "fine" in {s.qualname for s in parsed.symbols}


def test_javascript_module_extensions() -> None:
    parsed = ts("export function f() {}\n", path="lib/mod.mjs")
    assert symbol(parsed, "f").key == "js:lib/mod.mjs:f:function"


def test_output_is_deterministic() -> None:
    code = "export class A { m() { helper(); } }\n"
    assert ts(code).model_dump_json() == ts(code).model_dump_json()
