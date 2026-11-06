# M03 — Graph store & queries

**Type of work:** Data and persistence · **Size:** M (8 tasks) · **Status:** Not started

## Goal

Persist snapshots — graph and file text — in Postgres, and answer graph queries fast enough for an interactive map, without ever reading from a checkout.

## Depends on

- **M00** — database engine and sessions, Alembic, `db_session`/`pg_engine` fixtures, CLI.
- **M02** — `CodeGraph`, `Node`, `Edge`, `UnresolvedCall`, `Owner`, `build_graph`, canonical JSON and digest.
- (Through M02's inputs) **M01** — `fetch_source`, `fetch_history`, `build_inventory`, `parse_file`, `GitHubClient`.

## Provides

- **Migrations** for `repos`, `snapshots`, `blobs`, `files`, `nodes`, `edges`, `unresolved_calls`, `owners` (columns and indexes as in PRD §7.1).
- **`repoatlas.store.models`** — SQLAlchemy models for those tables.
- **`repoatlas.store.loader.save_snapshot(repo_meta, sha, fingerprint, inventory, blobs, graph) -> SnapshotId`** — one transaction, bulk `COPY`, idempotent for the same (repo, sha, fingerprint); marks the snapshot `ready` only after commit.
- **`repoatlas.store.graphstore.GraphStore`**
  - `get_node(snapshot_id, key)` · `find_symbols(snapshot_id, query, kinds=None, limit=20)` (exact, prefix, trigram) · `locate(snapshot_id, path, line)` (innermost node whose span contains the line)
  - `neighbors(snapshot_id, key, direction, kinds=None, depth=1)` · `callers(snapshot_id, key, depth)` · `callees(snapshot_id, key, depth)` · `shortest_path(snapshot_id, src, dst, kinds=None)`
  - `subgraph(snapshot_id, parent_key, level)` → `Subgraph(nodes, edges: list[AggregatedEdge(src, dst, kind, count, min_confidence, max_confidence)])`
  - `read_code(snapshot_id, path, start, end)` → `CodeSlice(path, blob_oid, start, end, text)` (only files in the snapshot; line cap)
  - `unresolved_calls(snapshot_id, name)` · `owners(snapshot_id, path)` · `snapshot(snapshot_id)`
- **`repoatlas.store.cache.SnapshotGraphCache`** — LRU of in-memory adjacency (forward and reverse, per edge kind) keyed by snapshot ID, with a memory cap; all traversals are cycle-safe.
- **`repoatlas.pipeline.build.build_snapshot(url, settings) -> SnapshotId`** — fetch → inventory → parse → graph → store, written as an ordered list of step functions so it can be split into stages.
- **Config fingerprint (initial):** `repoatlas.pipeline.fingerprint.compute(parts)` — sha256 over component versions (grammar versions, resolver and tier versions) and relevant settings (caps).
- **CLI:** `repoatlas build <url>`, `repoatlas query node|locate|callers|callees|path|subgraph|search …` (JSON output).

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

- [ ] **T1 — Schema.** Migrations and SQLAlchemy models; indexes (unique (`snapshot_id`, `symbol_key`); edges by src and dst; files by (`snapshot_id`, `path`); trigram on node names).
- [ ] **T2 — Loader.** `save_snapshot` with `COPY`, idempotency, failure marking; integration tests.
- [ ] **T3 — Cache and traversal.** Adjacency build, BFS with depth and kinds, cycle tests (self-loops, mutual recursion, large cycles).
- [ ] **T4 — Node reads.** `get_node`, `find_symbols`, `neighbors`, `callers`, `callees`, `shortest_path`, `owners`, `unresolved_calls`.
- [ ] **T5 — Subgraph aggregation.** Tests on the fixture graphs (root level, nested directory level, file level).
- [ ] **T6 — Locate and code reads.** Including a request for a path that is not in the snapshot (must fail) and line clamping.
- [ ] **T7 — Build pipeline and CLI.** `build_snapshot`, `repoatlas build`, `repoatlas query …`.
- [ ] **T8 — Round trip and performance.** Build → store → load equals the in-memory graph (same digest); benchmark script on a pinned mid-size repo; record timings in Results.

## Exit criteria

- [ ] Postgres integration tests pass.
- [ ] Round trip: the graph loaded from the store has the same digest as the graph that was saved.
- [ ] Traversals terminate and return correct results on cyclic graphs.
- [ ] On a pinned mid-size repo (warm cache): callers to depth 3 under 200 ms, subgraph under 150 ms.
- [ ] `repoatlas build <url>` followed by `repoatlas query callers …` works end to end.

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

_Filled in at the gate._
