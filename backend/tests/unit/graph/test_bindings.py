from __future__ import annotations

from collections.abc import Mapping

from repoatlas.graph.bindings import BindingTable, ModuleResolvers
from repoatlas.graph.model import EdgeKind, Provenance
from repoatlas.graph.project import ProjectFiles
from repoatlas.graph.targets import ModuleRef, Target
from repoatlas.parsing.models import Range
from tests.unit.graph.helpers import edge, graph_of, pairs, parse_files


def tables(
    files: Mapping[str, str], config: Mapping[str, str] | None = None
) -> dict[str, BindingTable]:
    output = parse_files(files)
    resolvers = ModuleResolvers.for_snapshot(
        output.inventory, output.files, ProjectFiles(files=dict(config or {}))
    )
    return {parsed.path: BindingTable.build(parsed, resolvers)[0] for parsed in output.files}


def symbol(key: str) -> Target:
    return Target.for_symbol(key, key.split(":")[1])


PY_FILES = {
    "pkg/__init__.py": "",
    "pkg/models.py": "class User:\n    pass\n\nclass Admin(User):\n    pass\n",
    "pkg/helpers.py": "__all__ = ['slug']\n\ndef slug(x):\n    return x\n\ndef hidden():\n    pass",
    "pkg/service.py": (
        "import os\n"
        "from pkg.models import User\n"
        "from pkg.models import Admin as User\n"
        "from pkg.helpers import *\n"
        "import pkg.models\n"
        "\n"
        "def handler():\n"
        "    from pkg import helpers as h\n"
        "    return h\n"
        "\n"
        "class Service:\n"
        "    from pkg.models import User as Inner\n"
    ),
}


def test_python_binding_tables_by_scope() -> None:
    table = tables(PY_FILES)["pkg/service.py"]
    assert table.lookup("os") == Target.for_external("os")
    assert table.lookup("User") == symbol("py:pkg/models.py:Admin:class")  # the later import wins
    assert table.lookup("pkg") == Target.for_module(ModuleRef("pkg/__init__.py", "pkg"))
    assert table.lookup("slug") == symbol("py:pkg/helpers.py:slug:function")
    assert table.lookup("hidden") is None  # not in __all__
    assert table.lookup("h") is None  # only inside handler
    handler = "py:pkg/service.py:handler:function"
    helpers = Target.for_module(ModuleRef("pkg/helpers.py"))
    assert table.lookup("h", [handler, None]) == helpers
    assert table.lookup("os", [handler, None]) == Target.for_external("os")
    service = "py:pkg/service.py:Service:class"
    assert table.lookup("Inner", [service]) == symbol("py:pkg/models.py:User:class")
    assert set(table.stars) == {"slug"}


def test_explicit_bindings_win_over_star_bindings() -> None:
    files = {
        "a.py": "def slug():\n    pass\n",
        "b.py": "def slug():\n    pass\n",
        "c.py": "from a import *\nfrom b import slug\n",
    }
    assert tables(files)["c.py"].lookup("slug") == symbol("py:b.py:slug:function")


TS_FILES = {
    "src/cart.ts": "export class Cart {}\nexport default function make() {}\n",
    "src/app.ts": (
        'import make, { Cart as Basket } from "./cart";\n'
        'import * as carts from "./cart";\n'
        'import express from "express";\n'
        'const legacy = require("./legacy");\n'
        "function load() {\n"
        '  const local = require("./cart");\n'
        "  return local;\n"
        "}\n"
    ),
    "src/legacy.js": "module.exports = function legacy() {};\n",
}


def test_typescript_binding_tables() -> None:
    table = tables(TS_FILES)["src/app.ts"]
    assert table.lookup("make") == symbol("ts:src/cart.ts:make:function")
    assert table.lookup("Basket") == symbol("ts:src/cart.ts:Cart:class")
    assert table.lookup("carts") == Target.for_module(ModuleRef("src/cart.ts"))
    assert table.lookup("express") == Target.for_external("express")
    assert table.lookup("legacy") == Target.for_module(ModuleRef("src/legacy.js"))
    assert table.lookup("local") is None
    load = "ts:src/app.ts:load:function"
    assert table.lookup("local", [load, None]) == Target.for_module(ModuleRef("src/cart.ts"))


def test_resolvers_look_up_module_members() -> None:
    output = parse_files({**TS_FILES, **PY_FILES})
    resolvers = ModuleResolvers.for_snapshot(output.inventory, output.files)
    assert resolvers.member(ModuleRef("src/cart.ts"), "Cart") == symbol("ts:src/cart.ts:Cart:class")
    assert resolvers.member(ModuleRef("pkg/__init__.py", "pkg"), "models") == Target.for_module(
        ModuleRef("pkg/models.py")
    )
    assert resolvers.called_module(ModuleRef("src/legacy.js")) == symbol(
        "js:src/legacy.js:legacy:function"
    )
    assert resolvers.called_module(ModuleRef("pkg/models.py")) is None


def test_import_edges_link_files_once_per_pair() -> None:
    graph = graph_of(
        {
            "src/cart.ts": "export class Cart {}\nexport const sum = 1;\n",
            "src/types.ts": "export interface Item {}\n",
            "src/admin.ts": "export const admin = 1;\n",
            "src/app.ts": (
                'import { sum } from "./cart";\n'
                'import express from "express";\n'
                'import { Cart } from "./cart";\n'
                'import type { Item } from "./types";\n'
                'const load = () => import("./admin");\n'
                'import "./missing";\n'
            ),
        }
    )
    assert pairs(graph, EdgeKind.IMPORTS) == {
        ("file:src/app.ts", "file:src/cart.ts"),
        ("file:src/app.ts", "file:src/types.ts"),
        ("file:src/app.ts", "file:src/admin.ts"),
    }
    cart = edge(graph, EdgeKind.IMPORTS, "file:src/app.ts", "file:src/cart.ts")
    assert cart.provenance == Provenance.IMPORT_RESOLVER and cart.confidence == 1.0
    assert cart.site == Range(start_line=1, start_col=0, end_line=1, end_col=29)
    assert cart.meta == {"count": 2}
    types = edge(graph, EdgeKind.IMPORTS, "file:src/app.ts", "file:src/types.ts")
    assert types.meta == {"count": 1, "type_only": True}
    admin = edge(graph, EdgeKind.IMPORTS, "file:src/app.ts", "file:src/admin.ts")
    assert admin.meta == {"count": 1, "dynamic": True}
    assert graph.stats.import_statements == 6
    assert graph.stats.imports_by_outcome == {"external": 1, "internal": 4, "missing": 1}


def test_python_import_edges() -> None:
    graph = graph_of(
        {
            "pkg/__init__.py": "from pkg.models import User\n",
            "pkg/models.py": "class User:\n    pass\n",
            "pkg/views.py": "def index():\n    pass\n",
            "app.py": (
                "from pkg import models, views\n"
                "from pkg import User\n"
                "import os\n"
                "from . import nothing\n"
                "\n"
                "def run():\n"
                "    import pkg.views\n"
            ),
        }
    )
    assert pairs(graph, EdgeKind.IMPORTS) == {
        ("file:pkg/__init__.py", "file:pkg/models.py"),
        ("file:app.py", "file:pkg/models.py"),
        ("file:app.py", "file:pkg/views.py"),
        ("file:app.py", "file:pkg/__init__.py"),
    }
    views = edge(graph, EdgeKind.IMPORTS, "file:app.py", "file:pkg/views.py")
    assert views.meta == {"count": 2} and views.site is not None and views.site.start_line == 1
    assert graph.stats.imports_by_outcome == {"external": 1, "internal": 4, "missing": 1}


def test_import_edges_follow_tsconfig_paths() -> None:
    graph = graph_of(
        {
            "src/lib/money.ts": "export const fmt = 1;\n",
            "src/app.ts": 'import { fmt } from "@/lib/money";\n',
        },
        {"tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'},
    )
    assert pairs(graph, EdgeKind.IMPORTS) == {("file:src/app.ts", "file:src/lib/money.ts")}


def test_files_never_import_themselves() -> None:
    graph = graph_of({"pkg/__init__.py": "from . import x\nfrom pkg import y\nx = y = 1\n"})
    assert pairs(graph, EdgeKind.IMPORTS) == set()
