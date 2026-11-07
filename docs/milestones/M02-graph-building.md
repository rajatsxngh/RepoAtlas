# M02 — Graph building

**Type of work:** Static analysis · **Size:** L (11 tasks) · **Status:** In progress

## Goal

Link parsed files into a deterministic, in-memory code graph where every edge records where it came from and how sure it is — without any database, network, or LLM.

## Depends on

- **M01** — `Inventory`/`FileEntry`, `ParsedFile` and its models (symbols, imports with bindings, call sites with ranges, class bases, route candidates, test cases), symbol-key functions, `HistoryStore`, `repoatlas parse`, fixture repos `py_small` and `ts_small`.

## Provides

- **`repoatlas.graph.model`**
  - `Node(key, kind, name, qualified_name, path, name_range, span, parent_key, exported, meta)`
  - `Edge(src, dst, kind, provenance, confidence, site: Range | None, meta)`
  - `UnresolvedCall(caller_key, callee_text, receiver_text, site, candidates: list[str], reason)`
  - `Owner(author_name, author_hash, commits, last_commit_at, share)`
  - `CodeGraph(nodes: dict[str, Node], edges: list[Edge], unresolved: list[UnresolvedCall], owners: dict[str, list[Owner]], stats: GraphStats)` with `digest()` and canonical `to_json()` / `from_json()`.
  - Enums `NodeKind` (`dir`, `file`, `class`, `function`, `method`, `route`, `test`), `EdgeKind` (`contains`, `imports`, `calls`, `inherits`, `tested_by`, `routes_to`), `Provenance` (`tree_sitter`, `import_resolver`, `name_match`, `convention`, `git`).
- **`repoatlas.graph.build.build_graph(inventory, parsed_files, history, settings) -> CodeGraph`** — pure and deterministic.
- **Resolvers** (usable on their own): `PythonModuleResolver`, `TsModuleResolver`, `BindingTable`, `CallResolver` (with the tier table as data in `graph/tiers.py`).
- **CLI:** `repoatlas graph <url|local-path> --json <out>` and `--stats`.
- **Golden files:** `expected.graph.json` for both fixture repos.

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
- [ ] **T3 — Python module resolver.** Tests for src layouts, namespace packages, relative imports, `__init__` re-exports, external imports.
- [ ] **T4 — TypeScript/JavaScript module resolver.** Tests for extensions, `index` files, `.js` → `.ts`, `tsconfig` `paths`/`baseUrl`/`extends`, re-export chains.
- [ ] **T5 — Bindings and import edges.**
- [ ] **T6 — Call resolver.** One test per tier plus ambiguity, unresolved, external, and constructor cases.
- [ ] **T7 — Inheritance edges.**
- [ ] **T8 — Test links.** Import-based, call-based, and convention-based `tested_by` edges.
- [ ] **T9 — Routes and public markers.** FastAPI, Flask, Express, Next.js fixtures.
- [ ] **T10 — Owners.** From a small fixture history (a scripted git repo created in the test).
- [ ] **T11 — Golden graphs, CLI, hand check.** `expected.graph.json` for both fixtures; `repoatlas graph`; build a pinned mid-size repo and hand-verify 20 randomly sampled edges (seeded sample) — record the table in Results.

## Exit criteria

- [ ] Resolver and tier unit tests pass.
- [ ] The golden graphs match exactly (same node and edge sets, same digest).
- [ ] Two builds of the same input produce identical digests.
- [ ] Hand check on a pinned mid-size repo: structural edges (`contains`, `imports`, `inherits`) at least 19 of 20 correct; `calls` edges reported per tier.
- [ ] `repoatlas graph --stats` prints node and edge counts by kind, unresolved and external call counts.

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

_Filled in at the gate._
