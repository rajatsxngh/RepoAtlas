from __future__ import annotations

from collections.abc import Mapping

import pytest

from repoatlas.graph.project import ProjectFiles, TsConfig, load_jsonc, ts_configs
from repoatlas.graph.targets import ModuleRef, ResolvedImport, Target
from repoatlas.graph.ts_resolver import TsModuleResolver
from repoatlas.parsing.models import ImportStmt
from tests.unit.graph.helpers import parse_files


def make(
    files: Mapping[str, str], config: Mapping[str, str] | None = None
) -> tuple[TsModuleResolver, dict[str, list[ImportStmt]]]:
    output = parse_files(files)
    paths = [entry.path for entry in output.inventory.files]
    configs = ts_configs(ProjectFiles(files=dict(config or {})))
    resolver = TsModuleResolver(output.files, paths, configs=configs)
    return resolver, {parsed.path: parsed.imports for parsed in output.files}


def symbol(key: str) -> Target:
    return Target.for_symbol(key, key.split(":")[1])


def module(path: str) -> Target:
    return Target.for_module(ModuleRef(path))


LAYOUT = {
    "src/main.ts": "",
    "src/a.ts": "",
    "src/b.tsx": "",
    "src/c.js": "",
    "src/d.jsx": "",
    "src/e.mjs": "",
    "src/f.cjs": "",
    "src/g.mts": "",
    "src/both.ts": "",
    "src/both.js": "",
    "src/lib/index.ts": "",
    "src/widgets/index.js": "",
    "src/x/y.ts": "",
    "src/util.ts": "",
    "src/types.d.ts": "",
}


@pytest.mark.parametrize(
    ("importer", "specifier", "expected"),
    [
        ("src/main.ts", "./a", "src/a.ts"),
        ("src/main.ts", "./b", "src/b.tsx"),
        ("src/main.ts", "./c", "src/c.js"),
        ("src/main.ts", "./d", "src/d.jsx"),
        ("src/main.ts", "./e", "src/e.mjs"),
        ("src/main.ts", "./f", "src/f.cjs"),
        ("src/main.ts", "./both", "src/both.ts"),
        ("src/main.ts", "./c.js", "src/c.js"),
        ("src/main.ts", "./both.js", "src/both.js"),
        ("src/main.ts", "./a.js", "src/a.ts"),
        ("src/main.ts", "./b.js", "src/b.tsx"),
        ("src/main.ts", "./b.jsx", "src/b.tsx"),
        ("src/main.ts", "./g.mjs", "src/g.mts"),
        ("src/main.ts", "./lib", "src/lib/index.ts"),
        ("src/main.ts", "./lib/", "src/lib/index.ts"),
        ("src/main.ts", "./widgets", "src/widgets/index.js"),
        ("src/main.ts", "./types", "src/types.d.ts"),
        ("src/x/y.ts", "../util", "src/util.ts"),
        ("src/x/y.ts", "..", None),
        ("src/x/y.ts", "../lib/index", "src/lib/index.ts"),
    ],
)
def test_relative_specifiers(importer: str, specifier: str, expected: str | None) -> None:
    resolver, _ = make(LAYOUT)
    outcome, found = resolver.resolve_module(importer, specifier)
    assert found == expected
    assert outcome == ("internal" if expected else "missing")


@pytest.mark.parametrize(
    ("specifier", "outcome"),
    [
        ("./nope", "missing"),
        ("./a/b/c", "missing"),
        ("./styles.css", "external"),
        ("./data.json", "external"),
        ("react", "external"),
        ("@scope/pkg/sub", "external"),
        ("node:fs", "external"),
        ("", "missing"),
    ],
)
def test_unresolved_specifiers(specifier: str, outcome: str) -> None:
    resolver, _ = make(LAYOUT)
    assert resolver.resolve_module("src/main.ts", specifier) == (outcome, None)


def test_load_jsonc_accepts_comments_and_trailing_commas() -> None:
    text = (
        '﻿{\n  // a comment with "quotes"\n'
        '  "compilerOptions": { /* block */ "baseUrl": ".", "paths": {"@/*": ["src/*",],}, },\n'
        '  "url": "http://not-a-comment", "q": "say \\"hi\\" // still a string",\n}\n'
    )
    assert load_jsonc(text) == {
        "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}},
        "url": "http://not-a-comment",
        "q": 'say "hi" // still a string',
    }
    assert load_jsonc("{ not json") is None


def test_base_url_resolves_bare_specifiers() -> None:
    files = {"src/app.ts": "", "src/utils/format.ts": ""}
    resolver, _ = make(files, {"tsconfig.json": '{"compilerOptions": {"baseUrl": "src"}}'})
    assert resolver.resolve_module("src/app.ts", "utils/format") == (
        "internal",
        "src/utils/format.ts",
    )
    assert resolver.resolve_module("src/app.ts", "react") == ("external", None)


def test_paths_patterns() -> None:
    files = {
        "src/app.ts": "",
        "src/cart.ts": "",
        "src/components/Button.tsx": "",
        "lib/index.ts": "",
        "special/Button.tsx": "",
    }
    config = """{
      "compilerOptions": {
        "baseUrl": ".",
        "paths": {
          "@/*": ["src/*"],
          "@/components/*": ["special/*"],
          "@lib": ["lib/index.ts"],
          "~/*": ["missing/*", "src/*"],
        },
      },
    }"""
    resolver, _ = make(files, {"tsconfig.json": config})
    resolve = resolver.resolve_module
    assert resolve("src/app.ts", "@/cart") == ("internal", "src/cart.ts")
    assert resolve("src/app.ts", "@/components/Button") == ("internal", "special/Button.tsx")
    assert resolve("src/app.ts", "@lib") == ("internal", "lib/index.ts")
    assert resolve("src/app.ts", "~/cart") == ("internal", "src/cart.ts")
    assert resolve("src/app.ts", "@/missing") == ("external", None)


def test_paths_without_base_url_are_relative_to_the_config() -> None:
    files = {"web/src/app.ts": "", "web/src/cart.ts": ""}
    config = {"web/tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'}
    resolver, _ = make(files, config)
    assert resolver.resolve_module("web/src/app.ts", "@/cart") == ("internal", "web/src/cart.ts")


def test_one_level_of_extends_is_followed() -> None:
    files = {"src/app.ts": "", "src/cart.ts": ""}
    base = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
    config = {
        "configs/tsconfig.base.json": base.replace('"."', '".."'),
        "tsconfig.json": '{"extends": "./configs/tsconfig.base.json"}',
    }
    resolver, _ = make(files, config)
    assert resolver.resolve_module("src/app.ts", "@/cart") == ("internal", "src/cart.ts")

    without_suffix = {"tsconfig.base.json": base, "tsconfig.json": '{"extends": "./tsconfig.base"}'}
    resolver, _ = make(files, without_suffix)
    assert resolver.resolve_module("src/app.ts", "@/cart") == ("internal", "src/cart.ts")

    two_levels = {
        "b.json": base,
        "a.json": '{"extends": "./b.json"}',
        "tsconfig.json": '{"extends": ["@tsconfig/node20/tsconfig.json", "./a.json"]}',
    }
    resolver, _ = make(files, two_levels)
    assert resolver.resolve_module("src/app.ts", "@/cart") == ("external", None)


def test_child_options_override_extended_ones() -> None:
    files = {"src/app.ts": "", "src/cart.ts": "", "lib/cart.ts": ""}
    config = {
        "base.json": '{"compilerOptions": {"baseUrl": "lib", "paths": {"@/*": ["*"]}}}',
        "tsconfig.json": '{"extends": "./base.json", "compilerOptions": {"baseUrl": "src"}}',
    }
    configs = ts_configs(ProjectFiles(files=config))
    assert configs[""] == TsConfig(
        path="tsconfig.json", base_url="src", paths=(("@/*", ("src/*",)),)
    )
    resolver, _ = make(files, config)
    assert resolver.resolve_module("src/app.ts", "@/cart") == ("internal", "src/cart.ts")


def test_the_nearest_config_applies() -> None:
    files = {"web/src/app.ts": "", "web/src/cart.ts": "", "api/app.ts": "", "api/cart.ts": ""}
    config = {
        "tsconfig.json": '{"compilerOptions": {"baseUrl": "."}}',
        "web/jsconfig.json": '{"compilerOptions": {"baseUrl": "src"}}',
        "web/tsconfig.json": '{"compilerOptions": {"baseUrl": "."}}',
    }
    resolver, _ = make(files, config)
    assert resolver.config_for("web/src/app.ts") == TsConfig("web/tsconfig.json", "web")
    assert resolver.resolve_module("web/src/app.ts", "src/cart") == ("internal", "web/src/cart.ts")
    assert resolver.resolve_module("api/app.ts", "api/cart") == ("internal", "api/cart.ts")
    js_only = {"web/jsconfig.json": '{"compilerOptions": {"baseUrl": "src"}}'}
    resolver, _ = make(files, js_only)
    assert resolver.resolve_module("web/src/app.ts", "cart") == ("internal", "web/src/cart.ts")
    assert resolver.config_for("api/app.ts") is None


BARREL = {
    "src/index.ts": (
        'export * from "./a";\n'
        'export { b as bee } from "./b";\n'
        'export * as ns from "./c";\n'
        'export { default } from "./d";\n'
        'export { default as Widget } from "./d";\n'
        'import { alpha } from "./a";\n'
        "export { alpha as renamed };\n"
        'export * from "./e";\n'
    ),
    "src/a.ts": "export function alpha() {}\nexport interface Shape { x: number }\n",
    "src/b.ts": "export const b = () => 1;\n",
    "src/c.ts": "export class C {}\n",
    "src/d.ts": "export default function () {}\n",
    "src/e.ts": "export default class E {}\nexport const value = 1;\n",
}


def test_reexport_chains_find_definitions() -> None:
    resolver, _ = make(BARREL)
    lookup = resolver.lookup_export
    assert lookup("src/index.ts", "alpha") == symbol("ts:src/a.ts:alpha:function")
    assert lookup("src/index.ts", "bee") == symbol("ts:src/b.ts:b:function")
    assert lookup("src/index.ts", "ns") == module("src/c.ts")
    assert lookup("src/index.ts", "default") == symbol("ts:src/d.ts:default:function")
    assert lookup("src/index.ts", "Widget") == symbol("ts:src/d.ts:default:function")
    assert lookup("src/index.ts", "renamed") == symbol("ts:src/a.ts:alpha:function")
    assert lookup("src/index.ts", "value") == Target.for_member("src/e.ts", "value")
    assert lookup("src/index.ts", "Shape") == Target.for_member("src/index.ts", "Shape")
    assert lookup("src/index.ts", "nothing") == Target.for_member("src/index.ts", "nothing")
    assert lookup("src/e.ts", "default") == symbol("ts:src/e.ts:E:class")
    assert resolver.exported_names("src/index.ts") == {
        "alpha",
        "bee",
        "ns",
        "default",
        "Widget",
        "renamed",
        "value",
    }


def test_export_star_never_forwards_default() -> None:
    files = {"src/index.ts": 'export * from "./d";\n', "src/d.ts": BARREL["src/d.ts"]}
    resolver, _ = make(files)
    assert resolver.lookup_export("src/index.ts", "default") == Target.for_member(
        "src/index.ts", "default"
    )


def test_reexport_chains_stop_after_three_hops() -> None:
    chain = {
        "src/index.ts": 'export * from "./one";\n',
        "src/one.ts": 'export { x } from "./two";\n',
        "src/two.ts": 'export * from "./three";\n',
    }
    three = {**chain, "src/three.ts": "export function x() {}\n"}
    resolver, _ = make(three)
    assert resolver.lookup_export("src/index.ts", "x") == symbol("ts:src/three.ts:x:function")
    four = {
        **chain,
        "src/three.ts": 'export { x } from "./four";\n',
        "src/four.ts": "export function x() {}\n",
    }
    resolver, _ = make(four)
    # The fourth hop is not followed: x stays a member of the module where the search stopped.
    assert resolver.lookup_export("src/index.ts", "x") == Target.for_member("src/two.ts", "x")


def test_export_cycles_terminate() -> None:
    files = {"src/a.ts": 'export * from "./b";\n', "src/b.ts": 'export * from "./a";\n'}
    resolver, _ = make(files)
    assert resolver.lookup_export("src/a.ts", "x") == Target.for_member("src/a.ts", "x")


def test_commonjs_exports_and_require_bindings() -> None:
    files = {
        "src/legacy.js": (
            "exports.join = function join() {};\n"
            "function describePath() {}\n"
            "module.exports.describePath = describePath;\n"
        ),
        "src/main.js": "module.exports = function main() {};\n",
        "src/app.js": (
            'const legacy = require("./legacy");\n'
            'const { join, describePath: dp } = require("./legacy");\n'
            'const main = require("./main");\n'
            'const fs = require("fs");\n'
        ),
        "src/esm.ts": 'import main from "./main";\nimport * as legacy from "./legacy";\n',
    }
    resolver, imports = make(files)
    results = [resolver.resolve_import("src/app.js", stmt) for stmt in imports["src/app.js"]]
    assert results[0] == ResolvedImport(
        "internal", ("src/legacy.js",), (("legacy", module("src/legacy.js")),)
    )
    assert results[1].bindings == (
        ("join", symbol("js:src/legacy.js:join:function")),
        ("dp", symbol("js:src/legacy.js:describePath:function")),
    )
    assert results[2].bindings == (("main", module("src/main.js")),)
    assert results[3] == ResolvedImport("external", (), (("fs", Target.for_external("fs")),))
    esm = [resolver.resolve_import("src/esm.ts", stmt) for stmt in imports["src/esm.ts"]]
    assert esm[0].bindings == (("main", symbol("js:src/main.js:main:function")),)
    assert esm[1].bindings == (("legacy", module("src/legacy.js")),)


def test_resolve_import_statement_kinds() -> None:
    files = {
        "src/app.ts": (
            'import express from "express";\n'
            'import type { Shape } from "./a";\n'
            'import "./polyfill";\n'
            'export { alpha } from "./a";\n'
            'export const load = () => import("./admin");\n'
            'import def, { alpha as al } from "./a";\n'
        ),
        "src/a.ts": BARREL["src/a.ts"] + "export default alpha;\n",
        "src/polyfill.ts": "",
    }
    resolver, imports = make(files)
    results = [resolver.resolve_import("src/app.ts", stmt) for stmt in imports["src/app.ts"]]
    assert results[0] == ResolvedImport(
        "external", (), (("express", Target.for_external("express")),)
    )
    assert results[1] == ResolvedImport(
        "internal", ("src/a.ts",), (("Shape", Target.for_member("src/a.ts", "Shape")),)
    )
    assert results[2] == ResolvedImport("internal", ("src/polyfill.ts",), ())
    assert results[3] == ResolvedImport("internal", ("src/a.ts",), ())  # re-exports bind nothing
    assert results[4] == ResolvedImport("missing", (), ())  # dynamic import of a missing file
    alpha = symbol("ts:src/a.ts:alpha:function")
    assert results[5].bindings == (("def", alpha), ("al", alpha))


def test_names_in_skipped_files_are_external() -> None:
    files = {
        "src/app.ts": 'import { x } from "../vendor/lib";\n',
        "vendor/lib.js": "exports.x = 1;\n",
    }
    resolver, imports = make(files)
    result = resolver.resolve_import("src/app.ts", imports["src/app.ts"][0])
    assert result.files == ("vendor/lib.js",)
    assert result.bindings == (("x", Target.for_external("vendor/lib.js")),)
