"""Incremental linking (PRF-2, decision 0024): the graph of a changed repository, linked on top
of the previous build, equals the graph linked from nothing.

Every invalidation rule has a scenario that goes wrong without it: with the rule switched off
the incremental graph differs from the full one, and with it they are equal. The scripted
change sets of the milestone (edit a body, add, delete, and rename a file, change an import,
add a class that a name-matched call now finds) each have a test, on both languages.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from repoatlas.graph.build import LinkResult
from repoatlas.graph.incremental import ALL_RULES, STALE, Rule
from repoatlas.graph.model import EdgeKind
from repoatlas.pipeline.changes import Rename
from tests.unit.graph.helpers import pairs, parse_files
from tests.unit.pipeline.incremental_helpers import Files, Linked, assert_same, link, relink

SHOP: dict[str, str] = {
    "pyproject.toml": "[project]\nname = 'shop'\n",
    "src/shop/__init__.py": "",
    "src/shop/util.py": "def helper():\n    return 1\n\n\ndef unused():\n    return 2\n",
    "src/shop/cart.py": (
        "from shop.util import helper\n\n\n"
        "class Cart:\n"
        "    def total(self):\n"
        "        return helper()\n\n"
        "    def add(self, item):\n"
        "        return self.total()\n"
    ),
    "src/shop/app.py": (
        "from shop.cart import Cart\n\n\ndef main():\n    cart = Cart()\n    return cart.add(1)\n"
    ),
    "src/shop/report.py": "def render(rows):\n    return [row.strip() for row in rows]\n",
    "tests/test_app.py": "from shop.app import main\n\n\ndef test_main():\n    assert main()\n",
}

WEB: dict[str, str] = {
    "package.json": '{"name": "web", "main": "src/index.ts"}\n',
    "tsconfig.json": '{"compilerOptions": {"baseUrl": "."}}\n',
    "src/index.ts": "export { total } from './cart';\nexport * from './format';\n",
    "src/cart.ts": (
        "import { round } from './format';\n\n"
        "export class Cart {\n"
        "  total(): number {\n"
        "    return round(this.sum());\n"
        "  }\n"
        "  sum(): number {\n"
        "    return 1;\n"
        "  }\n"
        "}\n\n"
        "export function total(cart: Cart): number {\n"
        "  return cart.total();\n"
        "}\n"
    ),
    "src/format.ts": "export function round(value: number): number {\n  return value;\n}\n",
    "src/page.ts": (
        "import { total } from './index';\nimport { Cart } from './cart';\n\n"
        "export function show(): number {\n  return total(new Cart());\n}\n"
    ),
    "src/__tests__/cart.test.ts": (
        "import { Cart } from '../cart';\n\n"
        "describe('cart', () => {\n"
        "  it('totals', () => {\n"
        "    expect(new Cart().total()).toBe(1);\n"
        "  });\n"
        "});\n"
    ),
}


def changed(
    base: Files, paths: Mapping[str, str | None] | None = None, **edits: str | None
) -> dict[str, str | bytes]:
    """``base`` with files replaced (text) or removed (``None``), named by path in ``paths`` or
    by keyword, where ``__`` stands for ``/`` and ``_dot_`` for ``.``."""
    files = dict(base)
    named = {name.replace("__", "/").replace("_dot_", "."): text for name, text in edits.items()}
    for path, text in {**(paths or {}), **named}.items():
        if text is None:
            del files[path]
        else:
            files[path] = text
    return files


# --- the milestone's scripted change sets ----------------------------------------------------


def test_nothing_changed_reuses_every_file() -> None:
    before = link(SHOP)
    again = relink(before, SHOP)
    assert again.changes.is_empty
    assert again.reasons == {}
    assert sorted(again.reused) == sorted(parsed.path for parsed in before.output.files)
    assert_same(again)


def test_editing_a_function_body_resolves_the_file_and_what_depends_on_it() -> None:
    before = link(SHOP)
    files = changed(SHOP, src__shop__report_dot_py="def render(rows):\n    return list(rows)\n")
    after = relink(before, files)
    assert after.changes.modified == ["src/shop/report.py"]
    # Nothing imports report.py and nothing calls a name it defines: only it is resolved.
    assert after.reasons == {"src/shop/report.py": "changed"}
    assert "src/shop/cart.py" in after.reused
    assert_same(after)


def test_editing_an_imported_file_resolves_its_dependents_at_any_depth() -> None:
    before = link(SHOP)
    files = changed(
        SHOP,
        src__shop__util_dot_py=(
            "def helper():\n    return unused()\n\n\ndef unused():\n    return 2\n"
        ),
    )
    after = relink(before, files)
    assert after.reasons == {
        "src/shop/util.py": "changed",
        "src/shop/cart.py": "dependency",  # imports util
        "src/shop/app.py": "dependency",  # imports cart, which imports util
        "tests/test_app.py": "dependency",
    }
    assert after.reused == ["src/shop/__init__.py", "src/shop/report.py"]
    assert_same(after)


def test_adding_a_file() -> None:
    before = link(SHOP)
    files = changed(
        SHOP,
        src__shop__tax_dot_py="from shop.util import helper\n\n\ndef tax():\n    return helper()\n",
    )
    after = relink(before, files)
    assert after.changes.added == ["src/shop/tax.py"]
    assert after.reasons["src/shop/tax.py"] == "changed"
    assert "src/shop/util.py" in after.reused
    assert_same(after)


def test_deleting_a_file() -> None:
    before = link(SHOP)
    after = relink(before, changed(SHOP, src__shop__util_dot_py=None))
    assert after.changes.deleted == ["src/shop/util.py"]
    # cart.py's import no longer resolves; app.py and the test reach cart.py.
    assert after.reasons["src/shop/cart.py"] == "imports"
    assert after.reasons["src/shop/app.py"] == "dependency"
    graph = after.incremental.graph
    assert not any("util.py" in edge.dst for edge in graph.edges)
    assert_same(after)


def test_renaming_a_file() -> None:
    before = link(SHOP)
    files = changed(
        SHOP, src__shop__util_dot_py=None, src__shop__helpers_dot_py=SHOP["src/shop/util.py"]
    )
    after = relink(before, files)
    assert after.changes.renamed == [Rename(old="src/shop/util.py", new="src/shop/helpers.py")]
    assert after.reasons["src/shop/helpers.py"] == "changed"
    assert after.reasons["src/shop/cart.py"] == "imports"
    assert_same(after)


def test_renaming_a_file_and_its_import() -> None:
    before = link(SHOP)
    files = changed(
        SHOP,
        src__shop__util_dot_py=None,
        src__shop__helpers_dot_py=SHOP["src/shop/util.py"],
        src__shop__cart_dot_py=SHOP["src/shop/cart.py"].replace("shop.util", "shop.helpers"),
    )
    after = relink(before, files)
    assert_same(after)
    calls = pairs(after.incremental.graph, EdgeKind.CALLS)
    assert any("helpers.py:helper:" in dst for _src, dst in calls)


def test_changing_an_import() -> None:
    before = link(SHOP)
    cart = SHOP["src/shop/cart.py"].replace(
        "from shop.util import helper", "from shop.util import unused as helper"
    )
    after = relink(before, changed(SHOP, src__shop__cart_dot_py=cart))
    assert after.reasons["src/shop/cart.py"] == "changed"
    assert_same(after)
    calls = pairs(after.incremental.graph, EdgeKind.CALLS)
    assert any("util.py:unused:" in dst for _src, dst in calls)


def test_adding_a_class_that_a_name_matched_call_now_finds() -> None:
    # report.py calls row.strip(): no class in the repository defines strip, so the call is
    # unresolved. A new class with a strip method makes it a name match.
    before = link(SHOP)
    assert not any(":Text.strip:" in edge.dst for edge in before.result.graph.edges)
    files = changed(
        SHOP, src__shop__text_dot_py="class Text:\n    def strip(self):\n        return self\n"
    )
    after = relink(before, files)
    assert after.reasons == {
        "src/shop/text.py": "changed",
        "src/shop/report.py": "name_match",
    }
    assert_same(after)
    calls = pairs(after.incremental.graph, EdgeKind.CALLS)
    assert any("text.py:Text.strip:" in dst for _src, dst in calls)


def test_a_second_definer_makes_a_name_match_ambiguous() -> None:
    first = changed(
        SHOP, src__shop__text_dot_py="class Text:\n    def strip(self):\n        return self\n"
    )
    before = link(first)
    files = changed(
        first, src__shop__line_dot_py="class Line:\n    def strip(self):\n        return self\n"
    )
    after = relink(before, files)
    assert after.reasons["src/shop/report.py"] == "name_match"
    assert_same(after)
    tiers = {
        edge.meta["tier"]
        for edge in after.incremental.graph.edges
        if edge.kind == EdgeKind.CALLS and ".strip:" in edge.dst
    }
    assert tiers == {"T6"}


def test_removing_a_definer_resolves_the_calls_that_named_it() -> None:
    first = changed(
        SHOP, src__shop__text_dot_py="class Text:\n    def strip(self):\n        return self\n"
    )
    before = link(first)
    after = relink(before, SHOP)
    assert after.changes.deleted == ["src/shop/text.py"]
    assert after.reasons == {"src/shop/report.py": "name_match"}
    assert_same(after)


# --- TypeScript ------------------------------------------------------------------------------


def test_typescript_body_edit() -> None:
    before = link(WEB)
    files = changed(
        WEB,
        src__format_dot_ts=(
            "export function round(value: number): number {\n  return Math.round(value);\n}\n"
        ),
    )
    after = relink(before, files)
    # Everything reaches format.ts: cart imports it, index re-exports it, page imports index.
    assert after.reasons["src/cart.ts"] == "dependency"
    assert after.reasons["src/page.ts"] == "dependency"
    assert_same(after)


def test_typescript_reexport_moves_to_another_file() -> None:
    before = link(WEB)
    files = changed(
        WEB,
        src__totals_dot_ts="export function total(cart: unknown): number {\n  return 0;\n}\n",
        src__index_dot_ts="export { total } from './totals';\nexport * from './format';\n",
    )
    after = relink(before, files)
    # page.ts is unchanged, but `total` now comes from totals.ts.
    assert after.reasons["src/page.ts"] in ("imports", "dependency")
    assert_same(after)
    calls = pairs(after.incremental.graph, EdgeKind.CALLS)
    assert any("totals.ts:total:" in dst for _src, dst in calls)


def test_typescript_added_index_file_and_deleted_test() -> None:
    before = link(WEB)
    files = changed(
        WEB,
        {"src/__tests__/cart.test.ts": None},
        src__lib__index_dot_ts="export function lib(): number {\n  return 1;\n}\n",
    )
    after = relink(before, files)
    assert after.changes.deleted == ["src/__tests__/cart.test.ts"]
    assert_same(after)
    assert not after.incremental.graph.edges_of(EdgeKind.TESTED_BY)


def test_project_configuration_change_is_seen_through_imports() -> None:
    files = changed(
        WEB,
        src__page_dot_ts=(
            "import { round } from '@lib/format';\n\n"
            "export function show(): number {\n  return round(1);\n}\n"
        ),
    )
    before = link(files)
    assert not any(
        "page.ts:show:" in edge.src for edge in before.result.graph.edges_of(EdgeKind.CALLS)
    )
    aliased = changed(
        files,
        tsconfig_dot_json='{"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["src/*"]}}}\n',
    )
    after = relink(before, aliased)
    # No source file changed: page.ts's import resolves now, and only the digest shows it.
    assert after.changes.modified == ["tsconfig.json"] or after.changes.is_empty
    assert after.reasons == {"src/page.ts": "imports"}
    assert_same(after)
    assert any(
        "page.ts:show:" in edge.src for edge in after.incremental.graph.edges_of(EdgeKind.CALLS)
    )


# --- one scenario per rule that goes wrong without it ----------------------------------------


def without(rule: Rule) -> frozenset[Rule]:
    return frozenset(ALL_RULES - {rule})


def test_rule_imports_is_needed_when_a_shadowing_package_goes_away() -> None:
    # `from util import helper` finds the package util/ first, which has no helper (so the call
    # is unresolved). Deleting the package lets the import reach util.py, which has one. app.py
    # is unchanged and reaches no changed file; only its import digest tells.
    files: dict[str, str] = {
        "util/__init__.py": "VALUE = 1\n",
        "util.py": "def helper():\n    return 1\n",
        "app.py": "from util import helper\n\n\ndef main():\n    return helper()\n",
    }
    before = link(files)
    assert not before.result.graph.edges_of(EdgeKind.CALLS)
    after_files = changed(files, {"util/__init__.py": None})

    broken = relink(before, after_files, rules=without("imports"))
    assert "app.py" in broken.reused
    assert not broken.equal

    after = relink(before, after_files)
    assert after.reasons == {"app.py": "imports"}
    assert_same(after)
    assert pairs(after.incremental.graph, EdgeKind.CALLS) == {
        ("py:app.py:main:function", "py:util.py:helper:function")
    }


def test_rule_dependency_is_needed_for_a_base_class_two_imports_away() -> None:
    # make() calls Base(), an alias of Bb, which extends Cc from another file. When Cc gains an
    # __init__, the call reaches it too. a.py is unchanged, its imports resolve as before, and
    # the call's name (Base) is nobody's symbol name: only the dependency rule finds it.
    files: dict[str, str] = {
        "c.py": "class Cc:\n    def m(self):\n        return 1\n",
        "b.py": "from c import Cc\n\n\nclass Bb(Cc):\n    pass\n",
        "a.py": "from b import Bb as Base\n\n\ndef make():\n    return Base()\n",
    }
    before = link(files)
    after_files = changed(
        files,
        c_dot_py=(
            "class Cc:\n    def __init__(self):\n        self.x = 1\n\n"
            "    def m(self):\n        return 1\n"
        ),
    )

    broken = relink(before, after_files, rules=without("dependency"))
    assert "a.py" in broken.reused
    assert not broken.equal

    after = relink(before, after_files)
    assert after.reasons == {"c.py": "changed", "b.py": "dependency", "a.py": "dependency"}
    assert_same(after)
    assert ("py:a.py:make:function", "py:c.py:Cc.__init__:method") in pairs(
        after.incremental.graph, EdgeKind.CALLS
    )


def test_rule_dependency_covers_a_module_found_by_path_in_a_package() -> None:
    # `pkg.sub.f()` finds pkg/sub.py by its path; app.py never imports it.
    files: dict[str, str] = {
        "pkg/__init__.py": "",
        "app.py": "import pkg\n\n\ndef main():\n    return pkg.sub.f()\n",
    }
    before = link(files)
    after_files = changed(files, pkg__sub_dot_py="def f():\n    return 1\n")
    after = relink(before, after_files)
    assert after.reasons == {"pkg/sub.py": "changed", "app.py": "dependency"}
    assert_same(after)

    # And the other way: deleting it again.
    back = relink(link(after_files), files)
    assert back.reasons["app.py"] in ("dependency", STALE)
    assert_same(back)


def test_rule_name_match_is_needed_for_a_file_that_imports_nothing() -> None:
    before = link(SHOP)
    files = changed(
        SHOP, src__shop__text_dot_py="class Text:\n    def strip(self):\n        return self\n"
    )
    broken = relink(before, files, rules=without("name_match"))
    assert "src/shop/report.py" in broken.reused
    assert not broken.equal
    assert_same(relink(before, files))


def test_name_match_follows_a_definer_whose_base_changed() -> None:
    # report.py calls x.build(); Maker.build is a class (a nested one), so the call also reaches
    # the constructor it inherits. base.py changes, maker.py does not: report.py must still be
    # resolved, because a definer of `build` can reach the changed file.
    files: dict[str, str] = {
        "base.py": "class Base:\n    pass\n",
        "maker.py": (
            "from base import Base\n\n\nclass Maker:\n    class build(Base):\n        pass\n"
        ),
        "report.py": "def run(x):\n    return x.build()\n",
    }
    before = link(files)
    after_files = changed(
        files, base_dot_py="class Base:\n    def __init__(self):\n        self.ready = True\n"
    )
    after = relink(before, after_files)
    assert after.reasons == {
        "base.py": "changed",
        "maker.py": "dependency",
        "report.py": "name_match",
    }
    assert_same(after)
    assert ("py:report.py:run:function", "py:base.py:Base.__init__:method") in pairs(
        after.incremental.graph, EdgeKind.CALLS
    )


# --- chains of builds, and records -----------------------------------------------------------


def test_incremental_builds_chain() -> None:
    """Each build is linked on top of the previous incremental one, not a full one."""
    steps: list[Files] = [
        changed(SHOP, src__shop__report_dot_py="def render(rows):\n    return list(rows)\n"),
        changed(SHOP, src__shop__util_dot_py=None),
        changed(
            SHOP, src__shop__text_dot_py="class Text:\n    def strip(self):\n        return self\n"
        ),
        SHOP,
    ]
    previous = link(SHOP)
    for files in steps:
        after = relink(previous, files)
        assert_same(after)
        previous = Linked(files, parse_files(files), after.incremental)


def test_records_hold_every_parsed_file_and_its_call_counts() -> None:
    linked = link(SHOP)
    records = linked.result.records
    assert sorted(records) == sorted(parsed.path for parsed in linked.output.files)
    counts: dict[str, int] = {}
    for record in records.values():
        for key, value in record.calls.items():
            counts[key] = counts.get(key, 0) + value
    stats = linked.result.graph.stats
    assert sum(counts.values()) == stats.call_sites
    assert {k: v for k, v in counts.items() if k.startswith("T")} == stats.call_sites_by_tier
    assert linked.result.stats() == {
        "files_resolved": len(records),
        "files_reused": 0,
        "by_rule": {},
    }


def test_a_file_without_a_record_is_resolved() -> None:
    before = link(SHOP)
    records = dict(before.result.records)
    del records["src/shop/report.py"]
    partial = Linked(before.files, before.output, LinkResult(before.result.graph, records))
    after = relink(partial, SHOP)
    assert after.reasons == {"src/shop/report.py": "changed"}
    assert_same(after)


def test_a_stored_edge_to_a_missing_node_is_never_restored() -> None:
    # With the imports rule off, cart.py would keep its edge to the deleted util.py. The edge's
    # target is gone, so the file is resolved after all.
    before = link(SHOP)
    after = relink(before, changed(SHOP, src__shop__util_dot_py=None), rules=frozenset({"changed"}))
    assert after.reasons["src/shop/cart.py"] == STALE
    keys = set(after.incremental.graph.nodes)
    assert all(edge.src in keys and edge.dst in keys for edge in after.incremental.graph.edges)


@pytest.mark.parametrize("base", [SHOP, WEB], ids=["python", "typescript"])
def test_stats_say_what_was_reused(base: Files) -> None:
    before = link(base)
    after = relink(before, base)
    stats = after.incremental.stats()
    assert stats["files_resolved"] == 0
    assert stats["files_reused"] == len(before.result.records)
