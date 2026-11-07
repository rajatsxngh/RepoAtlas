from __future__ import annotations

import pytest

from repoatlas.graph.model import CodeGraph, EdgeKind, NodeKind, Provenance
from repoatlas.graph.public import package_entries
from repoatlas.graph.routes import nextjs_pattern
from tests.unit.graph.helpers import edge, graph_of, pairs


def routes(graph: CodeGraph) -> dict[str, str | None]:
    """Route key → handler key."""
    handlers = {e.src: e.dst for e in graph.edges if e.kind == EdgeKind.ROUTES_TO}
    return {
        key: handlers.get(key) for key, node in graph.nodes.items() if node.kind == NodeKind.ROUTE
    }


def public(graph: CodeGraph) -> set[str]:
    return {key for key, node in graph.nodes.items() if node.meta.get("public") is True}


PYTHON_ROUTES = """\
from fastapi import APIRouter, FastAPI
from flask import Blueprint

app = FastAPI()
router = APIRouter()
bp = Blueprint("bp", __name__)


@app.get("/items/{item_id}")
async def read_item(item_id: int):
    return item_id


@router.post("/items")
def create_item():
    pass


@app.api_route("/both", methods=["GET", "PUT"])
def both():
    pass


@bp.route("/")
def index():
    pass


@bp.route("/submit", methods=["POST"])
def submit():
    pass


@cache.get("not-a-path")
def cached():
    pass
"""


def test_python_decorator_routes() -> None:
    graph = graph_of({"app/main.py": PYTHON_ROUTES})
    handler = "py:app/main.py:{}:function".format
    assert routes(graph) == {
        "route:app/main.py:GET /items/{item_id}": handler("read_item"),
        "route:app/main.py:POST /items": handler("create_item"),
        "route:app/main.py:GET /both": handler("both"),
        "route:app/main.py:PUT /both": handler("both"),
        "route:app/main.py:GET /": handler("index"),
        "route:app/main.py:POST /submit": handler("submit"),
    }
    route = graph.nodes["route:app/main.py:GET /items/{item_id}"]
    assert route.name == route.qualified_name == "GET /items/{item_id}"
    assert route.parent_key == "file:app/main.py" and route.path == "app/main.py"
    assert route.meta == {
        "method": "GET",
        "pattern": "/items/{item_id}",
        "source": "decorator",
        "public": True,
    }
    assert route.span is not None and route.span.start_line == 9
    link = edge(graph, EdgeKind.ROUTES_TO, route.key, handler("read_item"))
    assert (link.provenance, link.confidence) == (Provenance.TREE_SITTER, 0.95)
    assert ("file:app/main.py", route.key) in pairs(graph, EdgeKind.CONTAINS)


EXPRESS = {
    "src/handlers.ts": "export function listCarts() {}\nexport function removeCart() {}\n",
    "src/server.ts": (
        'import express from "express";\n'
        'import { listCarts } from "./handlers";\n'
        'import * as handlers from "./handlers";\n'
        "const app = express();\n"
        "function getCart() {}\n"
        'app.get("/carts/:id", getCart);\n'
        'app.get("/carts", listCarts);\n'
        'app.delete("/carts/:id", auth(), handlers.removeCart);\n'
        'app.post("/carts/:id/items", (req, res) => {\n'
        "  helper(req);\n"
        "  res.sendStatus(204);\n"
        "});\n"
        'app.put("/carts/:id", async (req, res) => { helper(req); });\n'
        'app.patch("/x", unknownHandler);\n'
        "function helper(x) { return x; }\n"
    ),
}


def test_express_routes_and_handlers() -> None:
    graph = graph_of(EXPRESS)
    assert routes(graph) == {
        "route:src/server.ts:GET /carts/%3Aid": "ts:src/server.ts:getCart:function",
        "route:src/server.ts:GET /carts": "ts:src/handlers.ts:listCarts:function",
        "route:src/server.ts:DELETE /carts/%3Aid": "ts:src/handlers.ts:removeCart:function",
        "route:src/server.ts:POST /carts/%3Aid/items": "ts:src/server.ts:<anon>:function",
        "route:src/server.ts:PUT /carts/%3Aid": "ts:src/server.ts:<anon>:function~2",
        "route:src/server.ts:PATCH /x": None,
    }
    same_file = edge(
        graph,
        EdgeKind.ROUTES_TO,
        "route:src/server.ts:GET /carts/%3Aid",
        "ts:src/server.ts:getCart:function",
    )
    assert same_file.provenance == Provenance.TREE_SITTER
    imported = edge(
        graph,
        EdgeKind.ROUTES_TO,
        "route:src/server.ts:GET /carts",
        "ts:src/handlers.ts:listCarts:function",
    )
    assert imported.provenance == Provenance.IMPORT_RESOLVER
    unknown = graph.nodes["route:src/server.ts:PATCH /x"]
    assert unknown.meta["handler"] == "unknownHandler" and unknown.meta["source"] == "call"


def test_inline_handlers_become_anonymous_functions() -> None:
    graph = graph_of(EXPRESS)
    anon = graph.nodes["ts:src/server.ts:<anon>:function"]
    assert (anon.kind, anon.name, anon.qualified_name) == (NodeKind.FUNCTION, "<anon>", "<anon>")
    assert anon.parent_key == "file:src/server.ts"
    assert anon.meta == {"signature": 'app.post("/carts/:id/items")', "inline_handler": True}
    assert anon.span is not None and (anon.span.start_line, anon.span.end_line) == (9, 12)
    calls = pairs(graph, EdgeKind.CALLS)
    helper = "ts:src/server.ts:helper:function"
    assert (anon.key, helper) in calls
    assert ("ts:src/server.ts:<anon>:function~2", helper) in calls
    assert ("file:src/server.ts", helper) not in calls
    callers = {u.caller_key for u in graph.unresolved if u.callee_text == "res.sendStatus"}
    assert callers == {anon.key}
    registration = {u.caller_key for u in graph.unresolved if u.callee_text == "app.post"}
    assert registration == {"file:src/server.ts"}


def test_inline_handlers_inside_functions_nest_under_them() -> None:
    graph = graph_of(
        {
            "routes.js": (
                "function helper() {}\n"
                "export function register(app) {\n"
                '  app.get("/a", (req, res) => helper());\n'
                "}\n"
            ),
        }
    )
    key = "js:routes.js:register.<anon>:function"
    assert routes(graph) == {"route:routes.js:GET /a": key}
    assert graph.nodes[key].parent_key == "js:routes.js:register:function"
    assert (key, "js:routes.js:helper:function") in pairs(graph, EdgeKind.CALLS)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("app/api/users/[id]/route.ts", ("nextjs_app", "/api/users/[id]")),
        ("src/app/(shop)/cart/route.js", ("nextjs_app", "/cart")),
        ("app/@modal/login/route.ts", ("nextjs_app", "/login")),
        ("app/route.ts", ("nextjs_app", "/")),
        ("apps/web/app/health/route.mjs", ("nextjs_app", "/health")),
        ("app/page.tsx", None),
        ("pages/api/hello.ts", ("nextjs_pages", "/api/hello")),
        ("src/pages/api/users/index.js", ("nextjs_pages", "/api/users")),
        ("pages/api/index.ts", ("nextjs_pages", "/api")),
        ("pages/api/_middleware.ts", None),
        ("pages/about.tsx", None),
    ],
)
def test_nextjs_patterns(path: str, expected: tuple[str, str] | None) -> None:
    assert nextjs_pattern(path) == expected


def test_nextjs_route_handlers() -> None:
    graph = graph_of(
        {
            "app/api/users/[id]/route.ts": (
                "export async function GET() {}\n"
                "const update = async () => {};\n"
                "export { update as PUT };\n"
                "export const helper = () => 1;\n"
            ),
            "app/api/empty/route.ts": "export const dynamic = 'force-dynamic';\n",
            "pages/api/hello.ts": "export default function handler(req, res) {}\n",
        }
    )
    assert routes(graph) == {
        "route:app/api/users/[id]/route.ts:GET /api/users/[id]": (
            "ts:app/api/users/[id]/route.ts:GET:function"
        ),
        "route:app/api/users/[id]/route.ts:PUT /api/users/[id]": (
            "ts:app/api/users/[id]/route.ts:update:function"
        ),
        "route:pages/api/hello.ts:ALL /api/hello": "ts:pages/api/hello.ts:handler:function",
    }
    link = edge(
        graph,
        EdgeKind.ROUTES_TO,
        "route:pages/api/hello.ts:ALL /api/hello",
        "ts:pages/api/hello.ts:handler:function",
    )
    assert (link.provenance, link.confidence, link.site) == (Provenance.CONVENTION, 0.95, None)
    assert graph.nodes["route:pages/api/hello.ts:ALL /api/hello"].meta["source"] == "nextjs_pages"


def test_top_level_package_all_marks_symbols_public() -> None:
    graph = graph_of(
        {
            "src/shop/__init__.py": (
                "from shop.cart import Cart\nfrom shop.app import create_app\n\n"
                "__all__ = ['Cart', 'create_app', 'VERSION']\nVERSION = '1'\n"
            ),
            "src/shop/cart.py": "class Cart:\n    pass\n\nclass Hidden:\n    pass\n",
            "src/shop/app.py": "def create_app():\n    pass\n",
            "src/shop/sub/__init__.py": "from shop.cart import Hidden\n__all__ = ['Hidden']\n",
        }
    )
    assert public(graph) == {
        "py:src/shop/cart.py:Cart:class",
        "py:src/shop/app.py:create_app:function",
    }


def test_package_json_entries_mark_exports_public() -> None:
    files = {
        "src/index.ts": (
            'export * from "./cart";\nexport { createServer as default } from "./server";\n'
        ),
        "src/cart.ts": "export class Cart {}\nexport function sum() {}\nfunction hidden() {}\n",
        "src/server.ts": "export function createServer() {}\n",
        "src/extra.ts": "export function extra() {}\n",
        "dist/index.js": "module.exports = {};\n",
    }
    config = {
        "package.json": (
            '{"main": "./dist/index.js", "types": "./dist/index.d.ts",'
            ' "exports": {".": {"types": "./dist/index.d.ts", "import": "./dist/index.mjs"},'
            ' "./extra": "./dist/extra.js", "./*": "./dist/*.js",'
            ' "./package.json": "./package.json"}}'
        ),
        "node_modules/dep/package.json": '{"main": "src/cart.ts"}',
    }
    graph = graph_of(files, config)
    assert public(graph) == {
        "ts:src/cart.ts:Cart:class",
        "ts:src/cart.ts:sum:function",
        "ts:src/server.ts:createServer:function",
        "ts:src/extra.ts:extra:function",
    }


def test_package_entries() -> None:
    data = {
        "main": "lib/index.js",
        "module": "./esm/index.mjs",
        "exports": {
            ".": [{"types": "./x.d.ts", "require": "./cjs/index.cjs"}, "./lib/index.js"],
            "./*": "./lib/*.js",
            "./data": "./data.json",
        },
    }
    assert package_entries(data) == [
        "lib/index.js",
        "./esm/index.mjs",
        "./cjs/index.cjs",
        "./lib/index.js",
    ]
    assert package_entries({"name": "x"}) == []


def test_routes_are_public() -> None:
    graph = graph_of({"app/main.py": PYTHON_ROUTES})
    assert {key for key in public(graph) if key.startswith("route:")} == set(routes(graph))
