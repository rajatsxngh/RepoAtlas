# M01 — Ingestion & parsing

**Type of work:** Static analysis · **Size:** L (11 tasks) · **Status:** Done

## Goal

Turn a public GitHub URL into a deterministic inventory of source files and per-file parse results — symbols, imports, call sites, tests — that are byte-for-byte identical on Windows and Linux.

## Depends on

- **M00** — `Settings` (including `github_token`, `data_dir`), logging, Typer CLI (`app.add_typer`), pytest markers and fixtures, CI matrix (Linux + Windows).

## Provides

- **`repoatlas.ingest.errors`** — `IngestError` (a stable `code` plus a user-facing message) and its subclasses `InvalidRepoUrl`, `RepoNotFound`, `RepoPrivate`, `RepoEmpty`, `RepoTooLarge`, `RateLimited` (with `reset_at`), `GitHubError`, `FetchError`.
- **`repoatlas.ingest.github`**
  - `parse_repo_url(url: str) -> RepoRef` (`owner`, `name`, `full_name`, `clone_url`) — strict validation; raises `InvalidRepoUrl` with a reason.
  - `GitHubClient.from_settings(settings)`; `get_repo(ref) -> RepoMeta` (`default_branch`, `private`, `size_kb`, `html_url`, `archived`, `fork`) rejects private repos; `resolve_head(ref, branch) -> str` (commit SHA). Uses the token when set and retries 502/503/504.
  - `check_repo_size(meta, max_size_kb)` raises `RepoTooLarge`.
- **`repoatlas.ingest.source`** — `SourceTree` (`label`, `commit`, `entries() -> list[TreeEntry]`, `read(entry) -> bytes`; a context manager) with `GitSourceTree` (git objects), `DirectorySourceTree` (a local folder, such as the fixtures), and `MemorySourceTree` (tests); `TreeEntry(path, mode, oid, size)`; `blob_oid(data)`.
- **`repoatlas.ingest.fetch`**
  - `fetch_source(clone_url, sha, dest, *, label, timeout) -> GitSourceTree` — raw bytes for every file at the commit, from a depth-1 fetch into a bare repository (never a working tree).
  - `fetch_history(clone_url, sha, dest, *, depth, timeout) -> HistoryStore` — commits and trees without file contents (a blobless partial clone). `HistoryStore.git(*args)` runs queries; they must pass `--no-renames`, because rename detection downloads file contents.
  - `Workspace(base_dir, keep=False)` — a working directory under `data_dir`, removed on exit. Every git call is hermetic (decision 0002 addendum).
- **`repoatlas.ingest.inventory`**
  - `build_inventory(tree, *, max_source_files, max_file_bytes) -> Inventory` — `files` (`FileEntry(path, mode, blob_oid, size, language, is_test, loc, skip_reason)` sorted by path), `stats`, and `indexed` (the entries to parse). Over-cap trees raise `RepoTooLarge`.
  - `language_for(path)`, `is_test_path(path, language)`.
- **`repoatlas.parsing.models`** (frozen Pydantic, canonical JSON): `Range(start_line, start_col, end_line, end_col)` — 1-based lines and 0-based byte columns into the blob; `Symbol` (`key`, `kind`, `name`, `qualname`, `parent`, `name_range`, `span`, `signature`, `decorators`, `modifiers`, `exported`); `ImportStmt`, `ImportBinding`, `CallSite`, `ClassBase`, `RouteCandidate`, `TestCase`, `Export`, `ParseIssue`, `ParsedFile`.
  - `name_range` covers the token that names the symbol: an identifier or property, a string literal with its quotes (quoted keys, test titles), or the `default` keyword of an anonymous default export. `span` covers the whole definition, decorators included.
- **`repoatlas.parsing.parse_file(entry, data: bytes) -> ParsedFile`** — dispatches to `parsing.python` or `parsing.typescript` (TypeScript, TSX, JavaScript). Syntax errors become `ParseIssue`s (up to 20 kept per file; `issue_count` holds the total).
- **`repoatlas.parsing.symbols`** — `make_symbol_key(...)`, `parse_symbol_key(...)`, and `KeyAllocator` (`~2`, `~3`, … for same-named siblings): the grammar in PRD §7.3, with percent-encoding.
- **`repoatlas.canonical`** — `canonical_json`, `digest` (sha256 of the canonical JSON), `pretty_json`, `write_text_lf`.
- **`repoatlas.pipeline.parse`** — `parse_tree(tree, *, max_source_files, max_file_bytes) -> ParseOutput` and `run_parse(target, settings, *, commit=None) -> ParseOutput` (`source`, `commit`, `inventory`, `files`, `stats`, `digest`).
- **Settings:** `github_api_url`, `max_repo_size_kb`, `max_source_files`, `max_file_bytes`, `git_timeout_s`, `history_depth`.
- **CLI:** `repoatlas parse <url|local-path> [--commit SHA] [--json <out>|-] [--keep-workdir]`; the output includes an overall `digest` (sha256 of the canonical JSON).
- **Fixtures:** `backend/tests/fixtures/repos/py_small/` and `backend/tests/fixtures/repos/ts_small/`, with golden output in `backend/tests/fixtures/golden/<name>.parse.json`.

## Requirements

ING-1, ING-2, ING-3, ING-4, ING-5, ING-6, ING-7, ING-8, ING-9, ING-10, ING-11, ING-12, ING-13

## Scope

**In:** URL validation, GitHub metadata, fetching bytes and history, inventory and skip rules, Python and TypeScript/JavaScript extraction, symbol keys, canonical JSON, cross-OS determinism.

**Out:** resolving imports or calls across files, persistence, analyzing history (it is only fetched here), anything that executes repo code.

## Design notes

### URLs and metadata
- Accept `https://github.com/<owner>/<repo>` with optional `.git` and trailing slash. Reject other schemes and hosts, `www.` variants, userinfo, ports, queries, fragments, and extra path segments (`/tree/...`). Owner and repo must match GitHub's allowed characters.
- `GET /repos/{owner}/{repo}` for `private`, `default_branch`, `size`; `GET /repos/{owner}/{repo}/commits/{branch}` for the head SHA. Unauthenticated limit is 60 requests per hour; with a token it is much higher.
- HTTP calls are recorded as fixtures for tests (no network in unit tests).

### Fetching (first task is a spike)
- Requirement: raw bytes for each file at the pinned commit, plus commit history without file contents, without writing a working tree (which on Windows could rewrite line endings or fail on names NTFS rejects).
- Options to compare in the spike:
  1. Depth-1 clone with `--no-checkout` (all blobs at HEAD, no working tree), read through `git ls-tree -r -z <sha>` and `git cat-file --batch`; history from a separate `--filter=blob:none --bare` clone.
  2. The commit's source archive from GitHub streamed through Python's `tarfile` in memory (never extracted), with blob OIDs computed locally as `sha1(b"blob " + len + b"\0" + data)` and cross-checked against `git ls-tree` from a blobless history clone.
- Criteria: OIDs match `git ls-tree`; speed on a mid-size repo; works the same on Windows and Linux; simplest code. The decision becomes an addendum to decision 0002.
- Always: `-c core.autocrlf=false -c core.symlinks=false`, env `GIT_LFS_SKIP_SMUDGE=1` and `GIT_TERMINAL_PROMPT=0`, https only, no submodule recursion, timeouts on every git call, temp directories under `data_dir`.

### Inventory and skip rules
- Modes from `ls-tree`: `100644`/`100755` regular files; `120000` symlink → skip (`symlink`); `160000` submodule → skip (`submodule`).
- LFS pointer (content starts with `version https://git-lfs.github.com/spec/v1`) → skip (`lfs_pointer`).
- Binary (NUL byte in the first 8 KB) → skip (`binary`).
- Vendored directories → skip (`vendored`): `node_modules/`, `vendor/`, `third_party/`, `site-packages/`, `.venv/`, `venv/`, `dist/`, `build/`, `out/`, `.next/`, `coverage/`, `__pycache__/`.
- Generated → skip (`generated`): `*.min.js`, `*.bundle.js`, `*.map`, or a header within the first 5 lines containing `@generated`, `DO NOT EDIT`, or `autogenerated`.
- Declaration files `*.d.ts` → skip (`declaration`).
- Languages: `.py` → `py`; `.ts`, `.mts`, `.cts`, `.tsx` → `ts` (TSX grammar for `.tsx`); `.js`, `.mjs`, `.cjs`, `.jsx` → `js`. Everything else is not source and is not inventoried.
- Tests: Python `test_*.py`, `*_test.py`, files under `tests/` or `test/`, `conftest.py`; TS/JS `*.test.*`, `*.spec.*`, files under `__tests__/`.
- LOC counts non-blank lines. Files over `max_file_bytes` are skipped (`too_large`).

### Extraction
- **Python:** module docstring; classes (bases, decorators, methods, properties); functions and methods (async, nested, decorated, `@staticmethod`, `@classmethod`, `@property`); imports (`import a.b as c`, `from . import x`, `from ..pkg import y as z`, star imports recorded as such); `__all__` lists; call sites (callee text such as `foo`, `self.bar`, `pkg.mod.baz`, receiver text, enclosing symbol, range); route-decorator candidates (`@app.get("/x")`, `@router.post(...)`, `@bp.route(...)` — raw text only); pytest tests (`test_*` functions and methods of `Test*` classes, parametrize ids not expanded).
- **TypeScript/JavaScript:** function declarations; arrow functions and function expressions bound to `const`/`let`/`var` names or object properties; generator functions; classes (extends, implements, methods, getters/setters, static members); imports (named, default, namespace, type-only, side-effect, dynamic `import("x")` with a literal, `require("x")`); exports (named, default, `export … from`, `export * from`, `export =`); call sites (including `new Foo()`); route candidates (`app.get('/x', handler)`, `router.post(...)`; Next.js route files are detected from paths later); test cases from `describe`/`it`/`test` calls with literal titles.
- **Symbol keys:** built from the enclosing definition chain; same-named siblings get `~2`, `~3`, … in source order; anonymous functions are `<anon>` with the same numbering; test cases use their title path joined with ` > `.
- **Ranges:** every symbol has a name range and a full span; ranges are 1-based lines and 0-based UTF-8 byte columns.
- **Errors:** tree-sitter `ERROR`/`MISSING` nodes are counted into `ParseIssue` records; extraction continues for the rest of the file.

### Tree-sitter
- Pin exact versions of `tree-sitter` and the `tree-sitter-python`, `tree-sitter-typescript` (both the TypeScript and TSX languages), and `tree-sitter-javascript` wheels in `uv.lock`.
- Query and cursor APIs change between releases, so all direct tree-sitter calls live in one module (`parsing/ts_runtime.py`); upgrades touch only that module.

### Determinism
- Outputs sorted by path, then position. JSON is canonical (sorted keys, no floats, LF). The digest is sha256 over the canonical JSON.
- A CI job parses both fixtures on Linux and Windows and compares the digests with the committed `expected` digests.

## Tasks

- [x] **T1 — Fetch spike.** Implement both options on one mid-size public repo; compare against the criteria; write the decision addendum.
- [x] **T2 — URLs and GitHub metadata.** `parse_repo_url` (table-driven tests: valid and invalid forms), `GitHubClient` with recorded responses (public, private, not found, rate-limited).
- [x] **T3 — Fetcher.** The chosen source fetch plus the blobless history fetch, with git flags, timeouts, and cleanup.
- [x] **T4 — Inventory.** Modes, skip rules, language and test detection, LOC, blob OIDs, size caps (unit tests per rule).
- [x] **T5 — Models and symbol keys.** Pydantic models with canonical JSON; `make_symbol_key`/`parse_symbol_key` with round-trip and escaping tests.
- [x] **T6 — Tree-sitter runtime.** Pinned grammars; one wrapper module; smoke tests per language.
- [x] **T7 — Python extractor.** At least 25 test cases (decorators, nested and async functions, properties, relative and star imports, `__all__`, calls on `self`, route decorators, pytest classes).
- [x] **T8 — TypeScript/JavaScript extractor.** At least 25 test cases (arrow functions, generators, default and re-exports, type-only and dynamic imports, `require`, `new` calls, class members, `describe`/`it`).
- [x] **T9 — Fixtures and CLI.** `py_small` and `ts_small` fixture repos with golden JSON; `repoatlas parse` command.
- [x] **T10 — Cross-OS determinism.** CI job comparing digests on Linux and Windows.
- [x] **T11 — Real-repo smoke.** Script that parses two pinned public repos (one Python, one TypeScript); record file counts, skip counts, parse-issue rate, and runtime in Results. Not part of CI.

## Exit criteria

- [x] At least 25 extractor cases per language pass.
- [x] Golden JSON matches for both fixture repos.
- [x] The CI determinism job produces the same digests on Linux and Windows.
- [x] Both pinned real repos parse with parse issues in fewer than 1% of files.
- [x] Invalid and private repo URLs are rejected with clear error messages.

## Verification

```bash
cd backend
uv run pytest tests/unit/ingest tests/unit/parsing -q
uv run repoatlas parse tests/fixtures/repos/py_small --json ../.tmp/py_small.json
uv run repoatlas parse https://github.com/<owner>/<repo> --json ../.tmp/real.json
```

Manual: open the JSON for a real repo and spot-check 10 symbols and 10 call sites against the source on GitHub.

## Risks and open questions

- GitHub API limits without a token (mitigated by recorded fixtures in tests and an optional token).
- Grammar edge cases: Python decorators with complex expressions, TypeScript overloads and namespaces, JSX in `.js` files.
- Very large files and minified code that slip past the skip rules; caps catch most of it.
- Resolved: the spike chose the depth-1 git fetch (decision 0002 addendum).

## Results

Gate checked on 2026-11-07 on branch `m01-ingestion-parsing`.

**Tests.** `uv run poe check` (with Postgres) passes on Windows: 240 passed, 1 skipped (creating symlinks needs extra privileges on Windows; that test runs on Linux CI). The extractor suites have 30 Python and 32 TypeScript/JavaScript cases. A fixture test checks that every name range covers its symbol's name and lies inside its span, and that every call lies inside its enclosing symbol.

**Fixtures** (golden JSON in `backend/tests/fixtures/golden/`):

| Fixture | Parsed | Skipped | Symbols / imports / calls / tests / routes | Digest |
|---|---|---|---|---|
| `py_small` | 7 (py 7) | declaration 1, generated 1, vendored 1 | 20 / 14 / 34 / 2 / 2 | `31cf59ec616dfb6a…` |
| `ts_small` | 6 (ts 5, js 1) | declaration 1, generated 1, vendored 2 | 19 / 12 / 35 / 3 / 2 | `13b0ed333b8a253e…` |

**Real-repo smoke** (`uv run python scripts/smoke_parse.py` on this Windows machine; times include the fetch):

| Repo @ commit | Tree entries | Source files | Parsed | Skipped | Symbols | Imports | Calls | Tests | Routes | Files with parse issues | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `pallets/flask` @ `49943b5a9441` | 236 | 83 | 83 (py 83) | none | 1,622 | 648 | 3,961 | 392 | 304 | 0 (0.00%) | 3.7 s |
| `colinhacks/zod` @ `19c40d207228` | 744 | 524 | 522 (ts 513, js 9) | generated 2 | 4,997 | 1,462 | 49,837 | 2,603 | 0 | 4 (0.77%) | 11.8 s |

- Digests: flask `d64632fe011426a9…`, zod `bc7cd082a87abdca…`.
- Every zod parse issue comes from TypeScript variance annotations (`interface ZodType<out Output = unknown, …>`, `in T`), which tree-sitter-typescript 0.23.2, the newest release on PyPI, cannot parse. The errors stay inside the type-parameter lists: with the annotations removed, the four files give the same symbols, calls, and imports, and no issues. Logged in the backlog.

**Spot check.** Beyond the 10 symbols and 10 call sites the Verification section asks for, a script checked every symbol and call of both smoke repos against the fetched bytes, and a seeded sample of 10 symbols and 10 calls per repo was reviewed against the source. All of them are right:
- Each name range covers the symbol's name: exactly for all 1,622 flask symbols and 2,387 zod symbols, and with its quotes for 2,610 zod test titles and quoted keys. Each lies inside its span.
- Each call lies inside its enclosing symbol, and its source text starts with the recorded callee (callee text turns `?.` into `.` on purpose).
- The first run found 66 zod symbols whose name range was wider than the name (anonymous `export default function` and `class`, and `a.b.c = function` assignments); they were fixed before the gate.

**Found and fixed at the gate**
- py-tree-sitter 0.26.0 crashed the interpreter (an access violation, exit 139) partway through flask's `src/flask/app.py`, at a different place on each run and never in a minimal script. The pin is now 0.25.2, on which everything passes; the pin comment and `parsing/ts_runtime.py` say to run the smoke before any tree-sitter upgrade.
- `parse_file` stripped a leading UTF-8 byte order mark, which shifted line-1 columns by 3 bytes. tree-sitter skips the mark itself while counting its bytes, so the stripping is gone and columns always index the blob.

**Other findings**
- Anonymous GitHub API calls from this machine returned 504s for a while during T2 while authenticated ones worked. The client retries 502/503/504 and its error suggests setting `REPOATLAS_GITHUB_TOKEN`; unit tests use recorded responses.

**Deviations from the design notes**
- Fetching uses the depth-1 git fetch the spike chose (decision 0002 addendum). History is a blobless partial clone, and history queries must pass `--no-renames`.
- `fetch_source` and `fetch_history` take the clone URL, and `build_inventory` takes the caps as keyword arguments instead of the settings object (see Provides).
- `dist/`, `build/`, `out/`, and `coverage/` count as vendored only at the top of the tree, because nested folders with those names are often real code; the other vendored names match at any depth. Also vendored: `bower_components/`, `_vendor/`, `.nuxt/`, `.tox/`. Also skipped: `.d.mts`, `.d.cts`, and `.pyi` declarations; `.min.mjs` and `.min.cjs`; headers saying "code generated by" or "auto-generated"; and minified files (over 20 KB with a line longer than 5,000 characters).
- Goldens live in `backend/tests/fixtures/golden/`, so the fixture folders hold only fixture code. Cross-OS determinism rests on the golden tests, which compare the full JSON (not only the digest) in both CI jobs; the CI jobs also print both fixture digests.

**CI.** Run [42334165583](https://github.com/rajatsxngh/RepoAtlas/actions/runs/42334165583) on `fd9a5f0` passed all three jobs: the Linux full check (241 passed, the symlink test included), the Windows unit tests (233 passed), and the docs lint. Both backend jobs printed `py_small` `31cf59ec616dfb6a…` and `ts_small` `13b0ed333b8a253e…`, the same digests as this machine.
