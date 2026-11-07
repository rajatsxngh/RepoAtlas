# M02 — Graph building

**Type of work:** Static analysis · **Size:** L (11 tasks) · **Status:** Done

## Goal

Link parsed files into a deterministic, in-memory code graph where every edge records where it came from and how sure it is — without any database, network, or LLM.

## Depends on

- **M01** — `Inventory`/`FileEntry`, `ParsedFile` and its models (symbols, imports with bindings, call sites with ranges, class bases, route candidates, test cases), symbol-key functions, `HistoryStore`, `repoatlas parse`, fixture repos `py_small` and `ts_small`.

## Provides

- **`repoatlas.graph.model`** (frozen Pydantic; collections kept in canonical order on construction)
  - `Node(key, kind, name, qualified_name, path, name_range, span, parent_key, exported, meta)` — `dir` and `file` nodes have no ranges; `meta` holds `signature`, `decorators`, `modifiers`, `framework`/`runner_id` (tests), `language`/`is_test`/`blob_oid`/`loc`/`skip_reason` (files), `unresolved_bases` (classes), `public`, and route fields.
  - `Edge(src, dst, kind, provenance, confidence, site: Range | None, meta)` — confidence in (0, 1], rounded to four places; `site` lies in the source node's file, except for `tested_by`, whose evidence lies in the test file; `calls` carry `meta.tier`, `meta.count` (and `meta.candidates` for T5/T6), `imports` carry `meta.count` (and `type_only`/`dynamic` when every statement is).
  - `UnresolvedCall(caller_key, callee_text, receiver_text, site, candidates: list[str], reason)` — reason `ambiguous`, `not_found`, `unbound_name`, or `dynamic`; at most 20 candidate keys.
  - `Owner(author_name, author_hash, commits, last_commit_at, share)` — `last_commit_at` is UTC; `author_hash` is a truncated sha256 of the normalized email.
  - `CodeGraph(nodes: dict[str, Node], edges: list[Edge], unresolved: list[UnresolvedCall], owners: dict[str, list[Owner]], stats: GraphStats)` with `digest()`, canonical `to_json()` / `from_json()`, and `edges_of(kind)`. `owners` is keyed by file path.
  - `GraphStats` — nodes and edges by kind, call sites by tier, unresolved calls by reason, external and builtin calls, import statements by outcome (`internal`, `external`, `missing`), history commits, files with owners.
  - Enums `NodeKind` (`dir`, `file`, `class`, `function`, `method`, `route`, `test`), `EdgeKind` (`contains`, `imports`, `calls`, `inherits`, `tested_by`, `routes_to`), `Provenance` (`tree_sitter`, `import_resolver`, `name_match`, `convention`, `git`).
- **`repoatlas.graph.build.build_graph(inventory, parsed_files, history=None, settings=None, *, project=None) -> CodeGraph`** — pure and deterministic. `history` is a `CommitLog`, `settings` a `GraphSettings(owner_commits)` (`GraphSettings.from_settings(settings)`), `project` a `ProjectFiles`. `GRAPH_VERSION` and `component_versions()` identify the builder and tier table for configuration fingerprints.
- **Resolvers** (usable on their own): `PythonModuleResolver`, `TsModuleResolver`, `BindingTable` and `ModuleResolvers` (`graph/bindings.py`), `NameResolver` (`graph/names.py`), `ClassHierarchy` (`graph/classes.py`), `CallResolver` (`graph/calls.py`), and the tier table and every edge confidence as data in `graph/tiers.py` (`Tier`, `TIER_TABLE`, `tier_confidence`, `TIERS_VERSION`).
- **`repoatlas.graph.owners`** — `read_commit_log(history_store, limit) -> CommitLog` (the only git access), `compute_owners(log, paths, limit)`, `author_hash(email)`, `CommitLog`/`CommitRecord`.
- **`repoatlas.graph.project`** — `ProjectFiles(files: dict[path, text])`, `python_roots`, `ts_configs`, `load_jsonc`.
- **`repoatlas.pipeline.graph`** — `read_project_files(tree, *, max_file_bytes) -> ProjectFiles`, `graph_tree(tree, …) -> GraphOutput`, and `run_graph(target, settings, *, commit=None, keep_workdir=False, with_history=True) -> GraphOutput(source, commit, parse_digest, graph, digest)`.
- **Settings:** `owner_commits` (default 1,000).
- **CLI:** `repoatlas graph <url|local-path> [--commit SHA] [--json <out>|-] [--stats] [--no-history] [--keep-workdir]`.
- **Golden files:** `backend/tests/fixtures/golden/<name>.graph.json` for both fixture repos (the `--json` output for the fixture folders).

## Requirements

GRA-1, GRA-2, GRA-3, GRA-4, GRA-5, GRA-6, GRA-7, GRA-8, GRA-9, GRA-10, GRA-11

## Scope

**In:** module resolution for Python and TypeScript/JavaScript, bindings, call resolution tiers, inheritance, directory hierarchy, test links, routes and public markers, owners, determinism, golden tests, a hand check of edges.

**Out:** persistence, HTTP, embeddings, compiler-grade indexers, anything that runs repo code.

## Design notes

### Nodes and hierarchy
- `dir` nodes for every directory that contains an inventoried file (root key `dir:.`), `file` nodes for every inventoried source file (skipped files get no symbol nodes).
- Symbols become `class`, `function`, `method`, or `test` nodes. Nested functions hang under their parent. Pytest tests and `describe`/`it` cases are `test` nodes (not `function`), so tests are easy to group.
- `contains` edges: dir → dir, dir → file, file → top-level symbols, class → methods, function → nested functions. Provenance `tree_sitter`, confidence 1.0.

### Python module resolution
- Candidate roots: repo root, `src/`, and directories named in `pyproject.toml` package settings when present.
- A module path maps to `pkg/sub/mod.py` or `pkg/sub/mod/__init__.py`; namespace packages (no `__init__.py`) are allowed.
- Relative imports resolve against the importing module's package.
- `from pkg import name`: if `pkg/name.py` (or a package) exists, it is a module import; otherwise `name` is looked up in `pkg/__init__.py`, following re-exports up to depth 3.
- Unresolvable imports are external (counted, no edge).

### TypeScript/JavaScript module resolution
- Relative specifiers try, in order: the exact path, then extensions `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, then `index.*` in a directory.
- ESM `.js` specifiers also try the `.ts`/`.tsx` source with the same base name.
- `tsconfig.json` (and `jsconfig.json`): `compilerOptions.baseUrl` and `paths` patterns, following one level of `extends`.
- Re-export chains (`export … from`, `export * from`) are followed up to depth 3 to find where a symbol is defined.
- Bare specifiers that don't match `paths` are external.

### Bindings and import edges
- Per file, a `BindingTable` maps local names to targets: a module (`file:` key), a symbol (symbol key), or a namespace import.
- `imports` edges go file → file (provenance `import_resolver`, confidence 1.0, `site` = the import statement's range). Symbol-level links come from call resolution.

### Call resolution tiers
Tiers and confidences live in `graph/tiers.py` (initial values; recalibrated later and recorded in a decision):

| Tier | Situation | Confidence |
|---|---|---|
| T1 | Callee bound by import to a resolved symbol, or defined in the same file scope | 0.95 |
| T2 | `alias.func()` where `alias` is an imported module or namespace with a resolved export | 0.90 |
| T3 | `self.m()`, `this.m()`, `cls.m()` resolved on the enclosing class or its resolved bases (method resolution order) | 0.85 |
| T4 | `super().m()` / `super.m()` resolved on a base class | 0.80 |
| T5 | `obj.m()` where exactly one class or module in the repo defines `m` | 0.50 |
| T6 | `obj.m()` with 2–5 candidates: one edge per candidate | 0.30 ÷ candidates |
| — | More than 5 candidates, or none in the repo | `UnresolvedCall` (no edge) |
| — | Bound to an external package or a builtin | Dropped, counted in stats |

- Constructor calls (`Foo()`, `new Foo()`) link to the class, with the tier of the binding; if the class defines `__init__` or `constructor`, a second edge links to it.
- Each `calls` edge records `meta.tier` and `site` (the call's range). Duplicate calls between the same pair collapse into one edge with `meta.count` and the first site.

### Inheritance
- `inherits` edges class → base class when the base resolves through bindings (confidence 0.95, provenance `import_resolver` or `tree_sitter` for same-file bases). Unresolved bases are listed in `meta.unresolved_bases`.

### Test links (`tested_by`, direction production → test)
1. A test file importing a production file: production `file` → test `file` (provenance `import_resolver`, confidence 0.9).
2. A test calling a production symbol: production symbol → `test` node (provenance `name_match`, confidence = the call's tier confidence).
3. Naming conventions: `tests/test_foo.py`, `test/foo_test.py` ↔ `foo.py` in the same package or matching path; `foo.test.ts`, `foo.spec.ts`, `__tests__/foo.ts` ↔ `foo.ts` (provenance `convention`, confidence 0.6).

### Routes and public markers
- FastAPI/Flask decorators on functions; Express `app.METHOD(path, handler)` and `router.METHOD(...)`; Next.js `app/**/route.(ts|js)` exporting `GET`/`POST`/…, and `pages/api/**` default exports.
- Each becomes a `route` node `route:<path>:<METHOD> <pattern>` with `routes_to` route → handler (confidence 0.95; inline handlers link to an `<anon>` function node).
- Public markers (`meta.public = true`): route nodes; symbols listed in a package's top-level `__init__.py` `__all__`; exports of files named by `package.json` `main`/`exports`.

### Owners
- From `HistoryStore`: `git log --no-renames --name-only --format=%H%x1f%an%x1f%ae%x1f%at -n <N> <sha>` (N defaults to 1,000). No blame, numstat, or rename detection: each would download file contents into the blobless history clone.
- Per path: authors with commit counts, last commit time, and share of commits. Emails are hashed (sha256, truncated) and never stored in clear text.

### Determinism
- All collections sorted (nodes by key; edges by src, dst, kind, site). Canonical JSON and sha256 digest, as for parsing.

## Tasks

- [x] **T1 — Graph model.** Dataclasses/Pydantic models, enums, canonical JSON, digest; round-trip tests.
- [x] **T2 — Structure.** `dir`/`file`/symbol nodes and `contains` edges from parse results.
- [x] **T3 — Python module resolver.** Tests for src layouts, namespace packages, relative imports, `__init__` re-exports, external imports.
- [x] **T4 — TypeScript/JavaScript module resolver.** Tests for extensions, `index` files, `.js` → `.ts`, `tsconfig` `paths`/`baseUrl`/`extends`, re-export chains.
- [x] **T5 — Bindings and import edges.**
- [x] **T6 — Call resolver.** One test per tier plus ambiguity, unresolved, external, and constructor cases.
- [x] **T7 — Inheritance edges.**
- [x] **T8 — Test links.** Import-based, call-based, and convention-based `tested_by` edges.
- [x] **T9 — Routes and public markers.** FastAPI, Flask, Express, Next.js fixtures.
- [x] **T10 — Owners.** From a small fixture history (a scripted git repo created in the test).
- [x] **T11 — Golden graphs, CLI, hand check.** `expected.graph.json` for both fixtures; `repoatlas graph`; build a pinned mid-size repo and hand-verify 20 randomly sampled edges (seeded sample) — record the table in Results.

## Exit criteria

- [x] Resolver and tier unit tests pass.
- [x] The golden graphs match exactly (same node and edge sets, same digest).
- [x] Two builds of the same input produce identical digests.
- [x] Hand check on a pinned mid-size repo: structural edges (`contains`, `imports`, `inherits`) at least 19 of 20 correct; `calls` edges reported per tier.
- [x] `repoatlas graph --stats` prints node and edge counts by kind, unresolved and external call counts.

## Verification

```bash
cd backend
uv run pytest tests/unit/graph -q
uv run repoatlas graph tests/fixtures/repos/ts_small --json ../.tmp/ts_small.graph.json
uv run repoatlas graph https://github.com/<owner>/<repo> --stats
```

Manual: the 20-edge hand check, comparing each sampled edge with the source on GitHub.

## Risks and open questions

- Dynamic imports, monkeypatching, decorators that wrap functions, and dependency injection create false or missing edges; tiers and the unresolved list keep that visible.
- Barrel files (`index.ts` re-exporting everything) can create long chains; depth caps bound the work.
- Very common method names (`get`, `run`, `update`) produce ambiguity; the candidate cap prevents edge explosions.

## Results

Gate checked on 2026-11-07 on branch `m02-graph-building`.

**Tests.** `uv run poe check` (with Postgres) passes on Windows: 428 passed, 1 skipped (creating symlinks needs extra privileges on Windows; that test runs on Linux CI). The graph suites have 188 tests: model 14, structure 11, Python resolver 24, TypeScript/JavaScript resolver 42, bindings 8, calls 19 (one per tier, plus ambiguous, every unresolved reason, external and builtin, constructors, collapsing, language families, and the name-matching rules below), inheritance 5, test links 23, routes and public markers 21, owners 7 (a scripted git history with four commits by two authors, and a shallow clone of it), golden 7, pipeline and CLI 7.

**Golden graphs** (`backend/tests/fixtures/golden/<name>.graph.json`, the `--json` output for the fixture folders; together they contain every edge kind):

| Fixture | Nodes | Edges | Call sites | Resolved by tier | Unresolved | External / builtin | Digest |
|---|---|---|---|---|---|---|---|
| `py_small` | 40 (dir 8, file 10, class 3, function 9, method 6, test 2, route 2) | 76 (contains 39, calls 19, tested_by 9, imports 6, routes_to 2, inherits 1) | 34 | T1 7, T3 1, T4 1, T5 1, T6 3 | 5 | 11 / 5 | `ec01fa4353017159…` |
| `ts_small` | 38 (dir 6, file 10, class 1, function 12, method 4, test 3, route 2) | 60 (contains 37, calls 11, tested_by 6, imports 4, routes_to 2) | 35 | T1 7, T2 2, T5 2 | 11 | 11 / 2 | `c066ffa9b52dbd10…` |

**Determinism.** Two builds of each fixture, and a build from the parse results in reverse order, give the same digest (`test_builds_are_deterministic`); the golden tests compare the full JSON on Linux and Windows. Two CLI builds of each real repo below gave identical digests: zod `2fd6bfacd34eca53…`, flask `45bec0d5b131baa8…`.

**Real repos** (`uv run repoatlas graph <url> --commit <sha> --stats` on this Windows machine; times include both fetches):

| Repo @ commit | Nodes | Edges | Call sites | Resolved by tier | Unresolved | External | Builtin | Imports internal / external | Owners | Time |
|---|---|---|---|---|---|---|---|---|---|---|
| `pallets/flask` @ `49943b5a9441` | 1,915 | 6,593 (calls 2,493, contains 1,914, tested_by 1,605, routes_to 314, imports 186, inherits 81) | 3,961 | T1 342, T2 329, T3 129, T4 6, T5 729, T6 621 | 688 | 601 | 516 | 310 / 338 | 63 files, 1,000 commits | 5.0 s |
| `colinhacks/zod` @ `19c40d207228` | 5,587 | 17,836 (calls 7,930, contains 5,586, tested_by 3,597, imports 676, inherits 43, routes_to 4) | 49,837 | T1 4,225, T2 2,514, T3 159, T5 5,175, T6 511 | 3,568 | 30,259 | 3,426 | 713 / 749 | 489 files, 1,000 commits | 9.2 s |

flask has 182 decorator routes (in `src`, tests, and examples), every one with a handler, and 27 public symbols; zod has 4 Next.js route handlers in `packages/docs` and 758 public symbols reached from its `package.json` entries.

**Hand check** on zod @ `19c40d2072288b4cafec851e9d056a72a88e5973` (524 source files): `uv run python scripts/sample_edges.py` (seed 2) draws 20 structural edges split across the kinds, and 5 `calls` edges per tier. Each was compared with the source at that commit (`https://github.com/colinhacks/zod/blob/19c40d2/<path>#L<line>`). Structural: **20 of 20 correct.**

| # | Kind | Edge | Evidence | OK |
|---|---|---|---|---|
| 1 | contains | `dir:packages/zod/src/v4/core/tests` → `index.test.ts` | the file is in that folder | ✓ |
| 2 | contains | `packages/bench/instanceof.ts` → `DATA` | `const DATA = makeData(1000, () => {` at top level, L13 | ✓ |
| 3 | contains | `packages/bench/compile-startswith.ts` → `startsWithSubstring` | top-level arrow function, L24 | ✓ |
| 4 | contains | `v4/classic/tests/registries.test.ts` → test "output type in registry meta - objects and arrays" | `test(…)` at L102 | ✓ |
| 5 | contains | `v3/tests/primitive.test.ts` → test "parse booleanSchema string" | `test(…)` at L227 | ✓ |
| 6 | contains | test "instanceof" (`v4/core/tests/compile.test.ts`) → class `instanceof.MyClass` | `class MyClass {` inside the test's callback, L871 | ✓ |
| 7 | contains | `v4/classic/tests/intersection.test.ts` → test "invalid object merge" | `test(…)` at L177 | ✓ |
| 8 | imports | `v4/classic/schemas.ts` → `v4/core/json-schema-processors.ts` | L4 `import * as processors from "../core/json-schema-processors.js"` | ✓ |
| 9 | imports | `v4/locales/uk.ts` → `v4/core/util.ts` | L3 `import * as util from "../core/util.js"` | ✓ |
| 10 | imports | `v4/classic/compat.ts` → `v4/classic/schemas.ts` | L4 `import type { ZodType } from "./schemas.js"` | ✓ |
| 11 | imports | `v4/locales/ur.ts` → `v4/core/checks.ts` | L1 `import type { $ZodStringFormats } from "../core/checks.js"` | ✓ |
| 12 | imports | `bench/jit-union.ts` → `bench/benchUtil.ts` | L1 `import { makeData, … } from "./benchUtil.js"` | ✓ |
| 13 | imports | `v4/locales/sl.ts` → `v4/core/checks.ts` | L1 `import type { $ZodStringFormats } from "../core/checks.js"` | ✓ |
| 14 | imports | `v3/helpers/parseUtil.ts` → `v3/locales/en.ts` | L3 `import defaultErrorMap from "../locales/en.js"` | ✓ |
| 15 | inherits | `ZodObject` → `ZodType` (`v3/types.ts`) | L2466 `> extends ZodType<Output, ZodObjectDef<…>, Input>` | ✓ |
| 16 | inherits | `ZodVoid` → `ZodType` | L2192 `class ZodVoid extends ZodType<void, ZodVoidDef, void>` | ✓ |
| 17 | inherits | `ZodNullable` → `ZodType` | L4536 `class ZodNullable<T …> extends ZodType<` | ✓ |
| 18 | inherits | `ZodRecord` → `ZodType` | L3515 `class ZodRecord<…> extends ZodType<` | ✓ |
| 19 | inherits | `ZodNever` → `ZodType` | L2163 `class ZodNever extends ZodType<never, ZodNeverDef, never>` | ✓ |
| 20 | inherits | `ZodString` → `ZodType` | L731 `class ZodString extends ZodType<string, ZodStringDef, string>` | ✓ |

Paths in rows 4–20 are under `packages/zod/src/` unless they start with `bench/` (`packages/bench/`). Every `.js` specifier in rows 8–14 resolves to the only `.ts` source with that name.

`calls` edges per tier, 5 sampled per tier (same seed), with flask @ `49943b5a9441` sampled the same way for a Python view:

| Tier | zod | flask | What the samples show |
|---|---|---|---|
| T1 | 5 / 5 | 5 / 5 | Same-file functions, nested-scope functions, and imported functions (flask's `from flask import Flask` through the package's re-export; `flaskr` through `examples/tutorial/pyproject.toml`). |
| T2 | 5 / 5 | 5 / 5 | Namespace members: zod's `z.object` through a three-hop re-export chain, `util.sizeCaps`; flask's `flask.Flask`, `flask.Blueprint`, `flask.abort`. |
| T3 | 5 / 5 | 5 / 5 | `this.m()`/`self.m()`, including methods inherited from a base class. |
| T4 | — | 5 / 5 | zod has no `super.m()` calls; flask's `super().__init__`/`super().loads` across files and through an intermediate base. |
| T5 | 0 / 5 | 4 / 5 | zod: four v4 calls reached v3 classes, because v4 attaches methods to instances inside `$constructor` callbacks that static extraction cannot see; one `val.toString()` on a number matched `ZodError.toString`. flask: the miss is `flask.request._get_current_object()`, matched to a `Protocol` stub while werkzeug's `LocalProxy` runs. |
| T6 | 1 / 5 | 1 / 5 | A T6 call spreads over two to five candidates, of which at most one is right; most sampled edges were the wrong sibling. |

Binding-based tiers (T1–T4) were right in all 35 sampled edges; name-matched tiers (T5, T6) are weak, as their initial confidences (0.5 and 0.30 ÷ candidates) already say. Calibration will refit them.

**Found and fixed at the gate** (from the flask, zod, and hand-check runs)
- Unknown receivers matched exported module-level functions (`items.map()` → a repo function `map`), and production code matched methods of classes defined in tests (`edb27b5`). After that, 9% of zod's T5/T6 edges still pointed at object-literal and namespace functions (`x.toString()` → `errorUtil.toString`), nearly all wrong, so name matching now considers only class methods and functions assigned onto objects or prototypes (`f9c40f7`).
- The hand check found `parse.safeParseAsync()`, where `parse` is a namespace import and `safeParseAsync` a factory-made value, name-matched at T5. A receiver that resolves to a module, to a class without that member, or to `super()` now gives an unresolved call; accessors are never name-matched (`39547e5`).
- flask had no public symbols: its `__init__.py` re-exports with `from .app import Flask as Flask` and defines no `__all__` (`7da0b57`).

**Deviations from the design notes**
- `build_graph` takes the commit log (`CommitLog`, read from the `HistoryStore` by `read_commit_log`) instead of the store, so it stays pure; it also takes `GraphSettings` and the project's configuration files (`ProjectFiles`, read from the source tree by `read_project_files`), which the inventory does not list.
- Name matching (T5/T6) considers instance members only: class methods (accessors excluded) and functions assigned onto objects or prototypes (`res.json = function`). Module-level, object-literal, and namespace functions are reached through bindings at T1/T2. Members defined in test files match only calls from tests, and classes defined inside functions only calls in their own file. A call whose receiver resolves but lacks the member is unresolved instead of name-matched. A bare name nothing binds is unresolved (`unbound_name`), with the module-level functions of that name as candidates.
- T2 also covers `Class.m()` on a class bound by import or defined in the same file; `a.b.m()` defined in the same file is T1. `super(…)` in a TypeScript constructor reaches the base constructor (or the base class) at T4.
- Constructor calls link the `__init__`/`constructor` that the class defines or inherits (the design said "defines").
- Python roots also include each nested `pyproject.toml`'s folder and its `src/`; absolute imports fall back to the importing file's folder when that folder is not a regular package (scripts and pytest folders).
- TypeScript/JavaScript resolution also tries `.mts`, `.cts`, and `.d.ts`, maps `.jsx`/`.mjs`/`.cjs` specifiers to `.tsx`/`.mts`/`.cts` sources, treats asset specifiers (`./app.css`, `./data.json`) as external, and counts other unmatched relative specifiers as missing.
- Route nodes hang under their file (`contains` file → route). Next.js `pages/api/**` routes use the method `ALL`; decorator routes need a path that starts with `/`.
- Public markers: without `__all__`, a top-level package's public names count (as `from package import *` binds them); `package.json` entries that point at build output (`dist/` and similar) fall back to `src/`.
- Import-based `tested_by` also links the file that defines a symbol imported through a package (`from shop import Cart` links `shop/cart.py`). Edges are kept per provenance, so a production file can link to the same test file by import and by convention.
- Owners are keyed by file path. Commits at the shallow boundary list no files, since git would report the whole tree as theirs. Email hashes are 16 hex characters of sha256.
- Goldens live in `backend/tests/fixtures/golden/<name>.graph.json`, next to the parse goldens, not as `expected.graph.json` in the fixture folders. `ts_small`'s `package.json` gained a `main` entry to exercise public markers; the parse golden is unchanged (`13b0ed333b8a253e…`).

**Other findings**
- The TypeScript extractor treats `const X = f(() => …)` as a wrapped function (meant for `memo(function …)`), so zod's `const DATA = makeData(1000, () => …)` is a function node. The structure is still correct (hand-check row 2); logged in the backlog.

**CI.** Run [42346503598](https://github.com/rajatsxngh/RepoAtlas/actions/runs/42346503598) on `f32fff8` passed all three jobs: the Linux full check (429 passed, the symlink test included), the Windows unit tests (421 passed), and the docs lint. Both backend jobs printed the same digests as this machine: graphs `py_small` `ec01fa4353017159…` and `ts_small` `c066ffa9b52dbd10…`, and parses `py_small` `31cf59ec616dfb6a…` and `ts_small` `13b0ed333b8a253e…`, unchanged since the previous gate.
