from __future__ import annotations

from collections.abc import Mapping

import pytest

from repoatlas.graph.project import ProjectFiles, join, python_roots
from repoatlas.graph.python_resolver import PythonModuleResolver
from repoatlas.graph.targets import ModuleRef, ResolvedImport, Target
from repoatlas.parsing.models import ImportStmt
from tests.unit.graph.helpers import parse_files


def make(
    files: Mapping[str, str], config: Mapping[str, str] | None = None
) -> tuple[PythonModuleResolver, dict[str, list[ImportStmt]]]:
    output = parse_files(files)
    paths = [entry.path for entry in output.inventory.files]
    roots = python_roots(ProjectFiles(files=dict(config or {})), paths)
    resolver = PythonModuleResolver(output.files, paths, roots=roots)
    return resolver, {parsed.path: parsed.imports for parsed in output.files}


def resolve(files: Mapping[str, str], importer: str, index: int = 0) -> ResolvedImport:
    resolver, imports = make(files)
    return resolver.resolve_import(importer, imports[importer][index])


def symbol(key: str) -> Target:
    path = key.split(":")[1]
    return Target.for_symbol(key, path)


SHOP = {
    "src/shop/__init__.py": "from shop.cart import Cart\n\n__all__ = ['Cart']\n",
    "src/shop/cart.py": "class Cart:\n    pass\n",
    "src/shop/utils/__init__.py": "",
    "src/shop/utils/helpers.py": "def money(v):\n    return v\n",
}


def test_join_normalizes_paths() -> None:
    assert join("", "src") == "src"
    assert join("a/b", "../c", "./d/") == "a/c/d"
    assert join("a", "..", "..") == ""


def test_src_layout() -> None:
    resolver, _ = make(SHOP)
    assert resolver.roots == ["", "src"]
    assert resolver.find_module("shop.cart") == ModuleRef("src/shop/cart.py")
    assert resolver.find_module("shop") == ModuleRef("src/shop/__init__.py", "src/shop")
    assert resolver.find_module("shop.utils.helpers") == ModuleRef("src/shop/utils/helpers.py")
    assert resolver.find_module("src.shop.cart") == ModuleRef("src/shop/cart.py")
    assert resolver.find_module("cart") is None


@pytest.mark.parametrize(
    ("config", "folder"),
    [
        ('[tool.setuptools.packages.find]\nwhere = ["lib"]\n', "lib"),
        ('[tool.setuptools]\npackage-dir = {"" = "python"}\n', "python"),
        ('[tool.setuptools]\npackage-dir = {"foo" = "legacy/foo"}\n', "legacy"),
        ('[tool.poetry]\npackages = [{ include = "foo", from = "code" }]\n', "code"),
        ('[tool.hatch.build.targets.wheel]\npackages = ["pkgs/foo"]\n', "pkgs"),
        ('[tool.pdm.build]\npackage-dir = "source"\n', "source"),
        ('[tool.maturin]\npython-source = "py"\n', "py"),
    ],
)
def test_pyproject_package_settings_add_roots(config: str, folder: str) -> None:
    files = {f"{folder}/foo/__init__.py": "", f"{folder}/foo/x.py": "def f():\n    pass\n"}
    resolver, _ = make(files, {"pyproject.toml": config})
    assert folder in resolver.roots
    assert resolver.find_module("foo.x") == ModuleRef(f"{folder}/foo/x.py")


def test_roots_ignore_malformed_pyproject_and_empty_folders() -> None:
    roots = python_roots(
        ProjectFiles(files={"pyproject.toml": "[tool.setuptools\nwhere = ["}), ["app.py"]
    )
    assert roots == [""]
    named = ProjectFiles(files={"pyproject.toml": '[tool.pdm.build]\npackage-dir = "nowhere"\n'})
    assert python_roots(named, ["src/a.py"]) == ["", "src"]


def test_nested_pyproject_projects_add_their_own_roots() -> None:
    files = {
        "services/api/src/api/__init__.py": "",
        "services/api/src/api/main.py": "def run():\n    pass\n",
        "services/worker/worker/jobs.py": "def go():\n    pass\n",
    }
    config = {"services/api/pyproject.toml": "[project]\nname = 'api'\n"}
    config["services/worker/pyproject.toml"] = "[project]\nname = 'worker'\n"
    resolver, _ = make(files, config)
    assert resolver.roots == [
        "",
        "services/api",
        "services/api/src",
        "services/worker",
    ]
    assert resolver.find_module("api.main") == ModuleRef("services/api/src/api/main.py")
    assert resolver.find_module("worker.jobs") == ModuleRef("services/worker/worker/jobs.py")


def test_namespace_packages() -> None:
    files = {"ns/sub/mod.py": "def f():\n    pass\n", "app.py": "from ns.sub import mod\n"}
    resolver, imports = make(files)
    assert resolver.find_module("ns.sub.mod") == ModuleRef("ns/sub/mod.py")
    namespace = ModuleRef(None, "ns/sub")
    assert resolver.find_module("ns.sub") == namespace
    assert resolver.lookup(namespace, "mod") == Target.for_module(ModuleRef("ns/sub/mod.py"))
    assert resolver.lookup(namespace, "nothing").kind == "missing"
    resolved = resolver.resolve_import("app.py", imports["app.py"][0])
    assert resolved.outcome == "internal"
    assert resolved.files == ("ns/sub/mod.py",)
    assert resolved.bindings == (("mod", Target.for_module(ModuleRef("ns/sub/mod.py"))),)


def test_packages_win_over_modules_and_modules_over_namespaces() -> None:
    files = {"a/__init__.py": "", "a.py": "", "b.py": "", "b/x.py": ""}
    resolver, _ = make(files)
    assert resolver.find_module("a") == ModuleRef("a/__init__.py", "a")
    assert resolver.find_module("b") == ModuleRef("b.py")


def test_relative_imports_resolve_against_the_package() -> None:
    files = {
        **SHOP,
        "src/shop/app.py": (
            "from .cart import Cart\n"
            "from . import utils\n"
            "from ..shop.cart import Cart as Again\n"
            "from .utils.helpers import money\n"
            "from .missing import thing\n"
            "from .. import top\n"
        ),
    }
    resolver, imports = make(files)
    stmts = imports["src/shop/app.py"]
    cart = resolver.resolve_import("src/shop/app.py", stmts[0])
    assert cart == ResolvedImport(
        "internal",
        ("src/shop/cart.py",),
        (("Cart", symbol("py:src/shop/cart.py:Cart:class")),),
    )
    utils = resolver.resolve_import("src/shop/app.py", stmts[1])
    package = ModuleRef("src/shop/utils/__init__.py", "src/shop/utils")
    assert utils.files == ("src/shop/utils/__init__.py",)
    assert utils.bindings == (("utils", Target.for_module(package)),)
    again = resolver.resolve_import("src/shop/app.py", stmts[2])
    assert again.bindings == (("Again", symbol("py:src/shop/cart.py:Cart:class")),)
    money = resolver.resolve_import("src/shop/app.py", stmts[3])
    assert money.bindings == (("money", symbol("py:src/shop/utils/helpers.py:money:function")),)
    missing = resolver.resolve_import("src/shop/app.py", stmts[4])
    assert missing.outcome == "missing" and missing.files == ()
    assert missing.bindings == (("thing", Target.for_missing(".missing")),)
    top = resolver.resolve_import("src/shop/app.py", stmts[5])
    assert top.outcome == "missing"
    assert top.bindings[0][1].kind == "missing"


def test_relative_imports_cannot_climb_above_the_repository() -> None:
    result = resolve({"x.py": "from .. import y\n", "y.py": ""}, "x.py")
    assert result.outcome == "missing"
    assert result.bindings == (("y", Target.for_missing("..")),)


def test_from_package_import_follows_init_reexports() -> None:
    files = {**SHOP, "tests/test_cart.py": "from shop import Cart\n"}
    result = resolve(files, "tests/test_cart.py")
    assert result.outcome == "internal"
    assert result.files == ("src/shop/__init__.py",)
    assert result.bindings == (("Cart", symbol("py:src/shop/cart.py:Cart:class")),)


def test_reexport_chains_stop_after_three_hops() -> None:
    chain = {
        "pkg/__init__.py": "from pkg.a import name\n",
        "pkg/a.py": "from pkg.b import name\n",
        "pkg/b.py": "from pkg.c import name\n",
    }
    three = {**chain, "pkg/c.py": "def name():\n    pass\n"}
    resolver, _ = make(three)
    package = ModuleRef("pkg/__init__.py", "pkg")
    assert resolver.lookup(package, "name") == symbol("py:pkg/c.py:name:function")
    four = {**chain, "pkg/c.py": "from pkg.d import name\n", "pkg/d.py": "def name():\n    pass\n"}
    resolver, _ = make(four)
    assert resolver.lookup(package, "name") == Target.for_member("pkg/c.py", "name")


def test_submodules_win_over_names_in_init() -> None:
    files = {
        "pkg/__init__.py": "name = 'shadowed by the submodule'\n",
        "pkg/name.py": "def f():\n    pass\n",
        "app.py": "from pkg import name, other\n",
    }
    result = resolve(files, "app.py")
    assert result.files == ("pkg/name.py", "pkg/__init__.py")
    assert result.bindings == (
        ("name", Target.for_module(ModuleRef("pkg/name.py"))),
        ("other", Target.for_member("pkg/__init__.py", "other")),
    )


def test_external_and_missing_absolute_imports() -> None:
    files = {
        **SHOP,
        "app.py": (
            "import os\n"
            "from decimal import Decimal\n"
            "import shop.nothing\n"
            "from shop.nothing import x\n"
            "import shop.cart as c\n"
            "import shop.cart\n"
        ),
    }
    resolver, imports = make(files)
    stmts = imports["app.py"]
    results = [resolver.resolve_import("app.py", stmt) for stmt in stmts]
    assert results[0] == ResolvedImport("external", (), (("os", Target.for_external("os")),))
    assert results[1].outcome == "external"
    assert results[1].bindings == (("Decimal", Target.for_external("decimal")),)
    assert results[2].outcome == "missing"
    shop = Target.for_module(ModuleRef("src/shop/__init__.py", "src/shop"))
    assert results[2].bindings == (("shop", shop),)
    assert results[3].outcome == "missing"
    assert results[3].bindings == (("x", Target.for_missing("shop.nothing")),)
    cart = Target.for_module(ModuleRef("src/shop/cart.py"))
    assert results[4] == ResolvedImport("internal", ("src/shop/cart.py",), (("c", cart),))
    assert results[5] == ResolvedImport("internal", ("src/shop/cart.py",), (("shop", shop),))


def test_star_imports_bind_public_names() -> None:
    files = {
        "pkg/__init__.py": "from .helpers import *\nfrom .other import *\n",
        "pkg/helpers.py": "__all__ = ['pub']\n\ndef pub():\n    pass\n\ndef also():\n    pass\n",
        "pkg/other.py": "def a():\n    pass\n\ndef _b():\n    pass\n",
    }
    resolver, imports = make(files)
    helpers = ModuleRef("pkg/helpers.py")
    assert resolver.public_names(helpers) == {"pub"}
    assert resolver.public_names(ModuleRef("pkg/other.py")) == {"a"}
    package = ModuleRef("pkg/__init__.py", "pkg")
    assert resolver.lookup(package, "pub") == symbol("py:pkg/helpers.py:pub:function")
    assert resolver.lookup(package, "a") == symbol("py:pkg/other.py:a:function")
    assert resolver.lookup(package, "also") == Target.for_member("pkg/__init__.py", "also")
    assert resolver.lookup(package, "_b") == Target.for_member("pkg/__init__.py", "_b")
    star = resolver.resolve_import("pkg/__init__.py", imports["pkg/__init__.py"][0])
    assert star == ResolvedImport("internal", ("pkg/helpers.py",), (), (helpers,))


def test_script_folders_resolve_sibling_modules_but_packages_do_not() -> None:
    files = {
        "scripts/run.py": "import helpers\n",
        "scripts/helpers.py": "def go():\n    pass\n",
        "pkg/__init__.py": "",
        "pkg/json.py": "",
        "pkg/user.py": "import json\n",
    }
    resolver, _ = make(files)
    assert resolver.find_module("helpers", importer="scripts/run.py") == ModuleRef(
        "scripts/helpers.py"
    )
    assert resolver.find_module("helpers") is None
    assert resolver.find_module("json", importer="pkg/user.py") is None
    assert resolve(files, "pkg/user.py").outcome == "external"


def test_names_in_skipped_files_are_external() -> None:
    files = {"vendor/lib.py": "def f():\n    pass\n", "app.py": "from vendor.lib import f\n"}
    result = resolve(files, "app.py")
    assert result.files == ("vendor/lib.py",)
    assert result.bindings == (("f", Target.for_external("vendor/lib.py")),)


def test_definitions_win_over_fallback_imports() -> None:
    files = {
        "helpers.py": (
            "try:\n    from orjson import dumps\n"
            "except ImportError:\n    def dumps(obj):\n        return obj\n"
        ),
    }
    resolver, _ = make(files)
    assert resolver.lookup(ModuleRef("helpers.py"), "dumps") == symbol(
        "py:helpers.py:dumps:function"
    )


def test_imports_never_link_a_file_to_itself() -> None:
    result = resolve({"pkg/__init__.py": "from . import x\nx = 1\n"}, "pkg/__init__.py")
    assert result.files == ()
    assert result.bindings == (("x", Target.for_member("pkg/__init__.py", "x")),)
