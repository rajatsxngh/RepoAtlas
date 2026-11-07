# M01 — Ingestion & parsing

**Type of work:** Static analysis · **Size:** L (11 tasks) · **Status:** In progress

## Goal

Turn a public GitHub URL into a deterministic inventory of source files and per-file parse results — symbols, imports, call sites, tests — that are byte-for-byte identical on Windows and Linux.

## Depends on

- **M00** — `Settings` (including `github_token`, `data_dir`), logging, Typer CLI (`app.add_typer`), pytest markers and fixtures, CI matrix (Linux + Windows).

## Provides

- **`repoatlas.ingest.github`**
  - `parse_repo_url(url: str) -> RepoRef` — strict validation; raises `InvalidRepoUrl` with a reason.
  - `GitHubClient.get_repo(ref) -> RepoMeta` (`default_branch`, `private`, `size_kb`, `html_url`) and `GitHubClient.resolve_head(ref, branch) -> str` (commit SHA). Uses the token when set; raises `RepoNotFound`, `RepoPrivate`, `RateLimited`.
- **`repoatlas.ingest.fetch`**
  - `fetch_source(ref, sha, workdir) -> SourceTree` — raw bytes for every file at the commit, read from git objects or the commit's source archive (never a checked-out working tree).
  - `fetch_history(ref, sha, workdir) -> HistoryStore` — commit history without file contents, queryable with git commands.
- **`repoatlas.ingest.inventory`**
  - `build_inventory(tree, settings) -> Inventory` — `FileEntry(path, mode, blob_oid, size, language, loc, is_test, skip_reason)` sorted by path.
  - Size caps from settings: `max_repo_size_kb`, `max_source_files`, `max_file_bytes`; over-cap repos raise `RepoTooLarge`.
- **`repoatlas.parsing.models`** (Pydantic, canonical JSON): `Range(start_line, start_col, end_line, end_col)`, `Symbol`, `ImportStmt`, `ImportBinding`, `CallSite`, `ClassBase`, `RouteCandidate`, `TestCase`, `ParseIssue`, `ParsedFile`.
- **`repoatlas.parsing.parse_file(entry, data: bytes) -> ParsedFile`** — dispatches to `parsing.python` or `parsing.typescript` (TypeScript, TSX, JavaScript).
- **`repoatlas.parsing.symbols.make_symbol_key(...)` / `parse_symbol_key(...)`** — the grammar in PRD §7.3, with percent-encoding.
- **CLI:** `repoatlas parse <url|local-path> [--commit SHA] --json <out>`; the output includes an overall `digest` (sha256 of the canonical JSON).
- **Fixtures:** `backend/tests/fixtures/repos/py_small/`, `backend/tests/fixtures/repos/ts_small/`, each with an `expected.parse.json`.

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
- [ ] **T2 — URLs and GitHub metadata.** `parse_repo_url` (table-driven tests: valid and invalid forms), `GitHubClient` with recorded responses (public, private, not found, rate-limited).
- [ ] **T3 — Fetcher.** The chosen source fetch plus the blobless history fetch, with git flags, timeouts, and cleanup.
- [ ] **T4 — Inventory.** Modes, skip rules, language and test detection, LOC, blob OIDs, size caps (unit tests per rule).
- [ ] **T5 — Models and symbol keys.** Pydantic models with canonical JSON; `make_symbol_key`/`parse_symbol_key` with round-trip and escaping tests.
- [ ] **T6 — Tree-sitter runtime.** Pinned grammars; one wrapper module; smoke tests per language.
- [ ] **T7 — Python extractor.** At least 25 test cases (decorators, nested and async functions, properties, relative and star imports, `__all__`, calls on `self`, route decorators, pytest classes).
- [ ] **T8 — TypeScript/JavaScript extractor.** At least 25 test cases (arrow functions, generators, default and re-exports, type-only and dynamic imports, `require`, `new` calls, class members, `describe`/`it`).
- [ ] **T9 — Fixtures and CLI.** `py_small` and `ts_small` fixture repos with golden JSON; `repoatlas parse` command.
- [ ] **T10 — Cross-OS determinism.** CI job comparing digests on Linux and Windows.
- [ ] **T11 — Real-repo smoke.** Script that parses two pinned public repos (one Python, one TypeScript); record file counts, skip counts, parse-issue rate, and runtime in Results. Not part of CI.

## Exit criteria

- [ ] At least 25 extractor cases per language pass.
- [ ] Golden JSON matches for both fixture repos.
- [ ] The CI determinism job produces the same digests on Linux and Windows.
- [ ] Both pinned real repos parse with parse issues in fewer than 1% of files.
- [ ] Invalid and private repo URLs are rejected with clear error messages.

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
- Open: which fetch option wins the spike.

## Results

_Filled in at the gate._
