# M03 — Graph store & queries

**Type of work:** Data and persistence · **Size:** M (8 tasks) · **Status:** In progress

## Goal

Persist snapshots — graph and file text — in Postgres, and answer graph queries fast enough for an interactive map, without ever reading from a checkout.

## Depends on

- **M00** — database engine and sessions, Alembic, `db_session`/`pg_engine` fixtures, CLI.
- **M02** — `CodeGraph`, `Node`, `Edge`, `UnresolvedCall`, `Owner`, `build_graph`, canonical JSON and digest.
- (Through M02's inputs) **M01** — `fetch_source`, `fetch_history`, `build_inventory`, `parse_file`, `GitHubClient`.

## Provides

- **Migration `0002_graph_store`** — `repos`, `snapshots`, `blobs`, `files`, `nodes`, `edges`, `unresolved_calls`, `owners` with the columns of PRD §7.1, plus four columns per range (`<name|span|site>_start_line`, `_start_col`, `_end_line`, `_end_col`), `nodes.path`, `files.mode` and `size_bytes`, `blobs.line_count`, `unresolved_calls.callee_name` (the callee's last identifier) and `reason`, and `snapshots.updated_at`.
  - Indexes: unique (`snapshot_id`, `symbol_key`); edges by (`snapshot_id`, `src_id`) and (`snapshot_id`, `dst_id`); unique files (`snapshot_id`, `path`); trigram GIN on `nodes.name` and `qualified_name`; `nodes.file_id`; unresolved calls by (`snapshot_id`, `callee_name`); unique snapshots (`repo_id`, `commit_sha`, `config_fingerprint`); unique repos (`host`, `lower(owner)`, `lower(name)`).
  - Only `snapshot_id` (and `snapshots.repo_id`) are foreign keys, with `ON DELETE CASCADE`; references inside a snapshot (`parent_id`, `src_id`, `dst_id`, `caller_id`, `file_id`) are written together by the loader. Check constraints pin snapshot statuses (`pending`, `building`, `ready`, `failed`), node and edge kinds, provenances, unresolved reasons, and confidence in (0, 1].
- **`repoatlas.store.models`** — `RepoRow`, `SnapshotRow`, `BlobRow`, `FileRow`, `NodeRow`, `EdgeRow`, `UnresolvedCallRow`, `OwnerRow`; `SnapshotId`, `SnapshotStatus`, `STORE_TABLES`. A test compares the models with the migrated database.
- **`repoatlas.store.loader`**
  - `save_snapshot(repo_meta, sha, fingerprint, inventory, blobs, graph, *, engine=None, extra_stats=None) -> SnapshotId` — `blobs` maps blob OID → bytes and may be lazy: only OIDs the store lacks are read, checked against their OID, and stored (shared by every snapshot). One transaction writes everything with `COPY` and sets `ready`; a failure marks the snapshot `failed` with the error in a separate transaction. A `ready` (repo, commit, fingerprint) is returned without writing anything, and concurrent saves are safe. `stats` holds `graph` (the `GraphStats`), `graph_digest`, `inventory`, `blobs` (`needed`, `stored`, `shared`, `stored_bytes`, `lossy`), `rows`, `store_seconds`, and the caller's `extra_stats` (which never override those).
  - `begin_snapshot(repo_meta, sha, fingerprint) -> SnapshotRef(id, status)` (gets or creates the row; one that is not `ready` becomes `building`), `find_snapshot(owner, name, sha, fingerprint)`, `mark_failed(snapshot_id, error)` (never downgrades `ready`), and `load_graph(snapshot_id) -> CodeGraph` (equal to the graph that was saved).
- **`repoatlas.store.graphstore.GraphStore(engine=None, cache=None, *, max_nodes=1000, max_read_lines=400)`** and `GraphStore.from_settings(settings, engine=None)`. Every read takes a snapshot ID and symbol keys and needs a `ready` snapshot. Errors come from `repoatlas.store.errors`, each with a stable `code`: `SnapshotNotFound`, `SnapshotNotReady` (with `status`), `NodeNotFound`, `PathNotInSnapshot`, `TextNotStored`, `InvalidQuery`. Results are frozen models in `repoatlas.store.results`, built from the graph's own `Node` and `Edge`:
  - `snapshot(id) -> SnapshotInfo` (any status) · `get_node(id, key) -> Node | None` · `find_symbols(id, query, kinds=None, limit=20) -> list[SymbolMatch(node, match, score)]` — `exact` (key, name, or qualified name, any case), then `prefix`, then `fuzzy` (trigram); limit up to 100 · `locate(id, path, line) -> Node` — the innermost class, function, method, or test containing the line (latest start, then earliest end, then method over function, test, and class), else the file.
  - `neighbors(id, key, direction, kinds=None, depth=1) -> Neighborhood(root, direction, kinds, depth, nodes: list[Hop(node, depth)], edges, truncated)` with `direction` `in`, `out`, or `both` and depth 1–20; `callers(id, key, depth=1)` and `callees(id, key, depth=1)` follow `calls` edges. At most `max_nodes` nodes come back (`truncated` is set past that). `shortest_path(id, src, dst, kinds=None) -> GraphPath(nodes, edges, directed, kinds) | None` — kinds default to `calls` and `imports`.
  - `subgraph(id, parent_key, level) -> Subgraph(parent, level, nodes, edges: list[AggregatedEdge(src, dst, kind, count, min_confidence, max_confidence)])`, memoized per (snapshot, parent, level): `dir` gives a directory's subdirectories and files, `file` every file below a directory, `symbol` the symbols directly inside a file or symbol. `count` is the number of graph edges.
  - `read_code(id, path, start, end) -> CodeSlice(path, blob_oid, start, end, text, line_count)` — only files of the snapshot; `start` clamps to 1 and `end` to the last line, at most `max_read_lines` lines; skipped files raise `TextNotStored` · `unresolved_calls(id, name)` (by last identifier or full callee text) · `owners(id, path)` · `graph(id) -> SnapshotGraph`.
- **`repoatlas.store.cache`** — `SnapshotGraph`: node keys in key order, kinds, and containment parents; edges as parallel arrays with compressed rows per edge kind in both directions; `out_edges`, `in_edges`, `traverse`, `shortest_path`, `children_of`, `descendants`, `aggregate`, and `from_code_graph`. `SnapshotGraphCache(max_mb)`: LRU by snapshot ID, evicted past the estimated cap (the newest entry always stays), and each snapshot is loaded once even when several threads ask at once. Every traversal is cycle-safe.
- **`repoatlas.store.blobs`** — `decode_blob(data) -> BlobText(text, lossy, line_count)`, `line_count`, `split_lines`: lines split on `\n` only, as tree-sitter counts rows; invalid UTF-8 and NUL become U+FFFD (`lossy`) without moving a line break.
- **`repoatlas.pipeline.build`** — `build_snapshot(url, settings, *, engine=None, commit=None, keep_workdir=False) -> SnapshotId` and `run_build(…, steps=BUILD_STEPS) -> BuildResult(snapshot_id, reused, repo, commit, fingerprint, timings)`. `BUILD_STEPS` is `resolve` (pins the commit, computes the fingerprint, gets or creates the snapshot; a ready one ends the build), `fetch`, `inventory`, `parse`, `graph`, `store`, each a `Step(name, run(ctx), settings_keys, components)` over a shared `BuildContext`. A failure in any step after `resolve` marks the snapshot `failed`. Also `fingerprint_parts(settings, steps)`, `config_fingerprint(settings, steps)`, and `TreeBlobs` (lazy blob bytes from a source tree).
- **Config fingerprint (initial):** `repoatlas.pipeline.fingerprint.compute(parts) -> str` — sha256 of the canonical JSON of `parts`. A build's parts list every step with the versions of what it runs (tree-sitter and grammar packages, `repoatlas.parsing.EXTRACTOR_VERSION`, `GRAPH_VERSION`, `TIERS_VERSION`) and the settings that change output (`history_depth`, `max_source_files`, `max_file_bytes`, `owner_commits`).
- **`repoatlas.pipeline.parse.parse_files(tree, inventory)`** — the parse loop on its own.
- **Settings:** `graph_cache_mb` (default 512), `max_read_lines` (default 400).
- **CLI:** `repoatlas build <url> [--commit SHA] [--keep-workdir]`; `repoatlas query snapshot|node|search|locate|callers|callees|neighbors|path|subgraph|code|owners|unresolved … --snapshot ID` (JSON on stdout, always UTF-8).

## Requirements

STO-1, STO-2, STO-3, STO-4, STO-5, STO-6, STO-7

## Scope

**In:** schema, migrations, bulk loading, blobs, read API, in-memory traversal, aggregation, synchronous build pipeline, debug CLI, performance measurement.

**Out:** HTTP endpoints, background jobs, UI, embeddings.

## Design notes

- **Blobs:** text stored as UTF-8. Files that aren't valid UTF-8 are decoded with replacement characters and flagged `lossy = true` (their line count is preserved). Blobs are shared across snapshots by OID, so a second snapshot of the same repo stores only changed files.
- **Row IDs vs symbol keys:** `nodes.id` is a per-snapshot surrogate; all external interfaces use symbol keys. `GraphStore` maps keys to IDs internally.
- **Why traversal is in memory:** recursive SQL on cyclic call graphs explodes in path counts. Loading a snapshot's edges into adjacency lists (a few hundred thousand edges at most within the caps) takes well under a second, and breadth-first search with visited sets is cycle-safe.
- **Cache:** keyed by snapshot ID; evicted least-recently-used when the estimated size exceeds `graph_cache_mb` (default 512).
- **Shortest path:** directed BFS from `src` to `dst` over the chosen kinds (default `calls`, `imports`); if none exists, an undirected search, flagged `directed = false` in the result.
- **Subgraph aggregation:** for `level = dir`, the children of `parent_key` are its immediate subdirectories and files; every edge in the snapshot is mapped to the pair of children that contain its endpoints; edges inside one child are dropped; counts and confidence ranges are aggregated per (src child, dst child, kind). Results for a (snapshot, parent) are memoized.
- **Locate:** the innermost node whose span contains the line, preferring `method` over `class` over `file`.
- **Transactions and failure:** the loader writes everything in one transaction. On failure the snapshot row is marked `failed` with the error in a separate transaction.
- **Performance targets (warm cache, mid-size repo):** `callers` to depth 3 under 200 ms; `subgraph` under 150 ms; cold load of a snapshot into the cache under 2 seconds.

## Tasks

- [x] **T1 — Schema.** Migrations and SQLAlchemy models; indexes (unique (`snapshot_id`, `symbol_key`); edges by src and dst; files by (`snapshot_id`, `path`); trigram on node names).
- [x] **T2 — Loader.** `save_snapshot` with `COPY`, idempotency, failure marking; integration tests.
- [x] **T3 — Cache and traversal.** Adjacency build, BFS with depth and kinds, cycle tests (self-loops, mutual recursion, large cycles).
- [x] **T4 — Node reads.** `get_node`, `find_symbols`, `neighbors`, `callers`, `callees`, `shortest_path`, `owners`, `unresolved_calls`.
- [x] **T5 — Subgraph aggregation.** Tests on the fixture graphs (root level, nested directory level, file level).
- [x] **T6 — Locate and code reads.** Including a request for a path that is not in the snapshot (must fail) and line clamping.
- [x] **T7 — Build pipeline and CLI.** `build_snapshot`, `repoatlas build`, `repoatlas query …`.
- [x] **T8 — Round trip and performance.** Build → store → load equals the in-memory graph (same digest); benchmark script on a pinned mid-size repo; record timings in Results.

## Exit criteria

- [x] Postgres integration tests pass.
- [x] Round trip: the graph loaded from the store has the same digest as the graph that was saved.
- [x] Traversals terminate and return correct results on cyclic graphs.
- [x] On a pinned mid-size repo (warm cache): callers to depth 3 under 200 ms, subgraph under 150 ms.
- [x] `repoatlas build <url>` followed by `repoatlas query callers …` works end to end.

## Verification

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend
uv run pytest tests/integration/store -q
uv run repoatlas build https://github.com/<owner>/<repo>
uv run repoatlas query callers --snapshot <id> "<symbol key>" --depth 3
uv run python scripts/bench_store.py --snapshot <id>
```

## Risks and open questions

- Memory use for the largest repos within the caps; the cache cap and file caps bound it.
- `COPY` through psycopg on Windows is expected to work; if not, fall back to `executemany` with batching.
- Open: whether to materialize aggregated edges for the root level if on-demand aggregation is too slow.

## Results

Gate checked on 2026-11-07 on branch `m03-graph-store`.

**Tests.** `uv run poe check` (with Postgres) passes on Windows: 579 passed, 1 skipped (creating symlinks needs extra privileges on Windows; that test runs on Linux CI). The Postgres integration suites: `tests/integration/store` 74 (schema 16, loader 19, graph store 15, subgraph 4, code reads 7, round trip 13) and `tests/integration/pipeline` 8. New unit suites: cache and traversal 19, subgraph aggregation 10, blob text and row mapping 21, fingerprint 19, and 3 CLI tests for UTF-8 output. `COPY` through psycopg works on Windows, so no `executemany` fallback was needed.

**Round trip.** Both fixture graphs come back from the store with their golden digests (`py_small` `ec01fa4353017159…`, `ts_small` `c066ffa9b52dbd10…`), and so do 12 seeded random graphs. Those exercise both kinds of range (with and without columns), every node and edge kind and provenance, nested meta, Unicode and percent-encoded keys, duplicate edges, unresolved calls with candidates, skipped files, and owners in other time zones. A graph built through `build_snapshot` (with history) loads back with the same digest as `run_graph` gives for that commit. On the real repos below, `load_graph` reproduces the digest recorded at save time, and both match M02's recorded digests (zod `2fd6bfacd34eca53…`, flask `45bec0d5b131baa8…`).

**Cycles.** Unit tests cover a self-loop (each direction), mutual recursion, and a 10,000-node ring (full traversal and paths around it). A stored snapshot with self-recursion, mutual recursion, and an import cycle checks the same cases through `GraphStore`. Every node of the three stored test snapshots gets `callers`, `callees`, and `neighbors` (both directions, all kinds) at depths 1 and 3, compared with a naive search straight over the saved edges. Every parent and level of both fixture graphs gets `subgraph`, compared with a direct implementation of the definition. Every line of every fixture file gets `locate`, also checked against the definition.

**End to end.** `uv run repoatlas build https://github.com/colinhacks/zod --commit 19c40d2072288b4cafec851e9d056a72a88e5973` stored snapshot 1 in 10.3 s (resolve 1.2 s, fetch 3.3 s, inventory 0.3 s, parse 3.2 s, graph 1.0 s, store 1.2 s): 5,587 nodes, 17,836 edges, 3,568 unresolved calls, and 521 distinct blobs for 522 indexed files. Building it again reused the snapshot after 0.5 s, without fetching. flask @ `49943b5a94413a530761350f04e896f76e8d620e` stored snapshot 3 in 4.8 s (1,915 nodes, 6,593 edges). `uv run repoatlas query callers --snapshot 1 "ts:packages/zod/src/v4/core/util.ts:normalizeParams:function" --depth 3` returns 648 callers (131 at depth 1, 299 at depth 2, 218 at depth 3) and the 1,081 edges followed. The pipeline test does the same through the CLI against a local origin served as GitHub, together with every other `query` command.

**Performance** on this Windows laptop (`uv run python scripts/bench_store.py --snapshot 1`, seed 3, 40 targets, warm reads timed 3 times after one untimed call). Times are wall-clock per call and include the Postgres queries that fetch the returned nodes and edges.

| zod @ `19c40d2` | Calls | p50 | p95 | Max | Notes |
|---|---|---|---|---|---|
| Cold load into the cache | 1 | 145 ms | | | 5,587 nodes, 17,836 edges, ~1.8 MB estimated (target under 2 s) |
| `load_graph` (round trip) | 1 | 808 ms | | | digest matches the saved one |
| `callers` depth 3, most-called symbols | 120 | 21.6 ms | 65.6 ms | 110.2 ms | median 119 callers, max 1,000 (one hit the node cap); target under 200 ms |
| `callers` depth 3, random symbols | 120 | 4.4 ms | 7.0 ms | 8.3 ms | median 0, max 15 callers |
| `subgraph` root, dir level (first / memoized) | 1 / 3 | 17.8 / 5.1 ms | | | 9 children, 6 aggregated edges |
| `subgraph` root, file level (first / memoized) | 1 / 3 | 48.7 / 25.3 ms | | | 524 children, 1,774 aggregated edges |
| `subgraph` largest directories, dir level (first) | 40 | 4.1 ms | 13.7 ms | 15.1 ms | up to 91 children, 140 aggregated edges |
| `subgraph` largest files, symbol level (first) | 40 | 6.8 ms | 13.7 ms | 39.0 ms | up to 175 children, 244 aggregated edges; target under 150 ms |
| `find_symbols` | 120 | 54.1 ms | 115.4 ms | 153.9 ms | names of the random symbols |
| `get_node` · `locate` · `read_code` | 120 each | 4.5 · 5.4 · 7.2 ms | 7.2 · 6.4 · 10.3 ms | 8.5 · 6.9 · 11.1 ms | |

flask (snapshot 3, same settings): cold load 71 ms; `callers` depth 3 on the most-called symbols p50 13.0 ms, p95 62.0 ms, max 113.5 ms; first `subgraph` calls at most 24.4 ms (root, file level: 83 files, 758 aggregated edges); `find_symbols` p95 30.3 ms.

The first benchmark run showed one `callers` call at 213 ms. Profiling the hubs gave about 60–80 ms in steady state (two fetches of about 13 ms each, and about 17 ms building 1,000 frozen models). The spike came from a first touch while the benchmark still held the fully loaded graph, tens of thousands of objects for the garbage collector to walk. The script now frees that graph and times warm calls after one untimed call.

**Found and fixed at the gate**
- Piping `repoatlas query` output on Windows raised `UnicodeEncodeError`: the text stdout uses the console code page (cp1252 when piped), and zod's JSON has non-ASCII names. JSON now goes to stdout's binary buffer as UTF-8, which also fixes the same latent bug in `parse --json -` and `graph --json -` (`135aa80`).
- A malformed `--commit` got a snapshot row before fetching rejected it; `resolve` now checks it first (`c547811`).
- `find_symbols` raised a bare `ValueError` for an unknown node kind; `extra_stats` could override the stats the store computes (`3fe5f97`).

**Deviations from the design notes**
- Extra columns (listed under Provides) carry what the round trip needs (byte columns of every range, node paths, unresolved reasons) and what reads use (`blobs.line_count`, `unresolved_calls.callee_name`). Node `meta` is stored verbatim, so file nodes repeat their language, test flag, OID, and line count from `files`.
- Only `snapshot_id` is a foreign key inside a snapshot: references among nodes and edges would each need an index for deletes to cascade, and every row would pay a referential check during `COPY`. Integrity comes from the loader, which refuses graphs whose edges, parents, callers, or owners name missing nodes or files.
- Blobs are stored for indexed files only. Skipped files keep their row and OID, so `files.blob_oid` has no foreign key, `read_code` raises `TextNotStored`, and `locate` returns the file.
- The adjacency cache holds topology only (about 1.8 MB for zod). Traversals fetch the nodes and edges they return from Postgres by row ID.
- `locate` skips route nodes, whose span is just the registration call, so a decorator line maps to its handler. Lines past the end of the file are rejected.
- Traversals return at most 1,000 nodes (`truncated`), with depth 1–20. `subgraph` also drops edges that leave the parent, and `contains` edges never appear.
- The build has a `resolve` step before `fetch`. It creates the snapshot as `building`, so a failure in any later step is recorded on it.
- The fingerprint is built per step from component versions and settings. `repoatlas.parsing.EXTRACTOR_VERSION` (new, 1) joins the graph and tier versions, and a test ties each version to its golden digests.
- `jsonb` would turn a float of 1e16 or more in `meta` (JSON writes it in exponent form) into an integer. The graph builder writes no such values, and the models docstring says so.

**Open question.** Materializing root-level aggregates is not needed. On zod the first root aggregation takes 18 ms (dir level) and 49 ms (file level, 524 files), and repeats are memoized.
