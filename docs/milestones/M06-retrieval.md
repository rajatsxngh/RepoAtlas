# M06 — Retrieval

**Type of work:** Search · **Size:** L (10 tasks) · **Status:** Done

## Goal

Find the right code for a question by combining semantic search, keyword search, and the code graph — with every result pointing at exact lines of a specific blob. No LLM is involved.

## Depends on

- **M03** — `blobs`, `files`, `nodes` tables; `GraphStore` (`neighbors`, `get_node`, `read_code`), `SnapshotGraphCache`.
- **M04** — stage registry (`register_stage`, `config_fingerprint`), job runner with progress, snapshot reuse.
- **M00** — settings, migrations, fixtures, CI.

## Provides

- **Migrations:** `chunks` (`id`, `snapshot_id`, `node_id`, `blob_oid`, `path`, `start_line`, `end_line`, `chunk_hash`, `header`, `text`, `tsv`), `embeddings` (`chunk_hash`, `model`, `dim`, `vector` — untyped pgvector; PK (`chunk_hash`, `model`)), and snapshot columns `embedding_model`, `embedding_dim`.
- **`repoatlas.retrieval.evidence.EvidenceRef`** — `(blob_oid, path, start_line, end_line, symbol_key | None)` with `to_compact()` (`path#L10-L24@abc1234`) and `from_compact()`. This is the one format for pointing at code.
- **`repoatlas.retrieval.chunker.chunk_snapshot(snapshot_id) -> list[Chunk]`** and a registered stage `chunk`.
- **`repoatlas.retrieval.embed`** — `Embedder` protocol (`model_name`, `dim`, `embed_documents(texts)`, `embed_query(text)`), `FastEmbedEmbedder(model_name)`, a deterministic `HashEmbedder` for tests; registered stage `embed` whose version and model name enter the fingerprint.
- **`repoatlas.retrieval.keyword`** — identifier splitting, `tsvector` construction, query builder.
- **`repoatlas.retrieval.retriever.Retriever`**
  - `retrieve(snapshot_id, query, mode="hybrid", budget_tokens=12000, k=…) -> RetrievalResult`
  - `RetrievalResult(evidence: list[EvidenceItem], debug: RetrievalDebug)`; `EvidenceItem(ref: EvidenceRef, text, score, sources: set["vector"|"keyword"|"graph"], node_key)`.
  - Modes: `vector`, `keyword`, `hybrid`; each step is a separate, testable function (vector search, keyword search, rank fusion, graph expansion, fusion rerank, budget packing).
- **CLI:** `repoatlas search --snapshot <id> "<query>" --mode hybrid --explain` (prints ranked evidence with per-source scores).
- **Fixture queries:** `backend/tests/fixtures/retrieval_queries.yaml` — labeled queries for the fixture repos with expected symbol keys.
- **Delivered details** (beyond the above, all backward compatible):
  - `Retriever(store: GraphStore, embedder: Embedder | None)` and `Retriever.from_settings(settings, engine)`; `k` defaults to 20 (`None` = budget only). Searching a snapshot embedded with another model raises `RetrievalError` (`code = "retrieval_unavailable"`); a snapshot without embeddings skips vector search in `hybrid` (noted in `debug.notes`) and raises in `vector`. A snapshot that is not ready raises `SnapshotNotReady`.
  - Step functions: `vector.vector_search(snapshot_id, query_vector, *, model, k)`, `keyword.keyword_search(snapshot_id, question, *, k)` and `keyword.build_query`, `fusion.reciprocal_rank_fusion(rankings)` and `select_seeds`, `expansion.expand(graph: SnapshotGraph, seeds)`, `rerank.rerank(candidates, query_names(question))`, `rerank.pack(ranked, budget_tokens, k)`. `RetrievalDebug` holds `vector`, `keyword`, `fused`, `seeds`, `expanded`, `ranked` (with skip reasons), `tokens`, `notes`, and `timings_ms`.
  - `chunkstore.save_chunks(snapshot_id, chunks)` and `load_chunks(snapshot_id, *, ids, node_ids) -> {id: StoredChunk}`; `embed.embed_chunks(snapshot_id, embedder)` and `embedder_for(settings)`; `chunker.chunk_graph(graph, read_text)` for in-memory graphs.
  - Settings `embedding_model` (default `jinaai/jina-embeddings-v2-base-code`; `hash-<dim>` selects `HashEmbedder`) and `embedding_threads`. Tests pin `hash-256` in `backend/tests/conftest.py` (`REPOATLAS_TEST_EMBEDDING_MODEL` overrides it).
  - Decision 0016: the stage order is `fetch`, `inventory`, `parse`, `graph`, `store`, `chunk`, `embed`, `publish`; `store` saves the snapshot as `building` and `publish` makes it ready (M03 and M04 Provides updated). The snapshot columns `embedding_model` and `embedding_dim` already existed (migration 0002); `embed` fills them.

## Requirements

RET-1, RET-2, RET-3, RET-4, RET-5, RET-6, RET-7, RET-8

## Scope

**In:** chunking, embeddings and their cache, keyword index, exact vector search, rank fusion, graph expansion, reranking, token-budget packing, debug CLI, fixture-based retrieval tests, latency measurement.

**Out:** any LLM call, answer generation, UI changes.

## Design notes

### Chunks
- One chunk per `function`, `method`, and `test` node, covering its span.
- A class chunk contains the class signature, docstring, and member signatures (not member bodies).
- A module chunk per file contains top-level code outside symbols, with long import blocks collapsed to one line per module.
- Chunks longer than ~1,200 tokens (estimated as characters ÷ 4) split at statement boundaries with a 10-line overlap; each part keeps the same node key.
- **Header** (prepended for embedding and keyword indexing, not stored as code lines): `path`, qualified name, signature — taken only from the chunk's own file, never from other files.
- `chunk_hash = sha256(model-independent text: header + body)`.
- Every chunk stores its `EvidenceRef` fields (`blob_oid`, `path`, `start_line`, `end_line`).

### Embeddings
- Default model: `jinaai/jina-embeddings-v2-base-code` through fastembed on CPU (768 dimensions). It is provisional; a later comparison can replace it, and the fingerprint ensures snapshots never mix models.
- Cache: before embedding, look up (`chunk_hash`, `model`) in `embeddings`; only missing chunks are embedded. Batches of 64; thread count configurable; progress reported to the stage.
- The model is downloaded on first use into `data_dir/models` (a large one-time download); tests use `HashEmbedder`, so CI never downloads it.
- Query embedding uses the same model and follows its query/document conventions.

### Vector search (exact, per snapshot)
```sql
SELECT c.id, 1 - (e.vector <=> :q) AS score
FROM chunks c
JOIN embeddings e ON e.chunk_hash = c.chunk_hash AND e.model = :model
WHERE c.snapshot_id = :snapshot
ORDER BY e.vector <=> :q
LIMIT :k;
```
- No approximate index in v1: an approximate index filters rows after its scan, which skews results when many snapshots share the table. Exact search over one snapshot's chunks is fast enough at these sizes; latency is measured (target under 300 ms for a mid-size repo).

### Keyword search
- Identifier splitting: `getUserById` → `get user by id`; `snake_case_name` → `snake case name`; `pkg.mod.func` → `pkg mod func`; raw identifiers are kept as tokens too.
- `tsv = to_tsvector('simple', header + split body)` with a GIN index; queries use `websearch_to_tsquery('simple', …)` over the split query; ranking with `ts_rank_cd`.

### Hybrid pipeline (defaults, to be tuned only on a development set)
1. Vector top 40 and keyword top 40.
2. Reciprocal rank fusion: `score = Σ 1 / (60 + rank)`.
3. Seeds: top 8 fused results.
4. Graph expansion from each seed's node: `calls`, `imports`, `inherits`, `tested_by`, `contains`, both directions, up to 2 hops; expanded score = seed score × edge confidence × 0.5^hop; deduplicate by node, keeping the best score.
5. Rerank: final score = max(fused score, expanded score) + a bonus when a query identifier exactly matches the node name.
6. Pack evidence in score order into the token budget (default 12,000 tokens), skipping chunks fully contained in an already-selected chunk.
- `RetrievalDebug` records every step's candidates and scores, so the CLI can explain results.

## Tasks

- [x] **T1 — Schema and evidence references.** Migrations; `EvidenceRef` with compact form and tests.
- [x] **T2 — Chunker and `chunk` stage.** Tests on the fixture repos (class chunks, module chunks, long-function splitting, headers from the same file only).
- [x] **T3 — Embedders and `embed` stage.** `FastEmbedEmbedder`, `HashEmbedder`, cache lookups, fingerprint includes the model; tests with `HashEmbedder`.
- [x] **T4 — Keyword index.** Identifier splitting (table-driven tests), `tsvector`, query builder, ranking.
- [x] **T5 — Vector search.** Exact per-snapshot search; integration test that results never leak across snapshots.
- [x] **T6 — Rank fusion and seeds.** Tests with synthetic rankings.
- [x] **T7 — Graph expansion.** Tests on the fixture graph (hop decay, confidence weighting, deduplication).
- [x] **T8 — Rerank and budget packing.**
- [x] **T9 — Search CLI.** `--explain` output with per-step scores.
- [x] **T10 — Retrieval checks and latency.** Fixture queries reach their expected symbols in the top k (with `HashEmbedder` for keyword/graph paths in CI, and the real model locally); latency on a pinned mid-size repo recorded in Results.

## Exit criteria

- [x] Unit tests pass for chunk boundaries, identifier splitting, rank fusion, and graph expansion.
- [x] With the real embedding model, at least 80% of fixture queries find an expected symbol in the top 5 in hybrid mode (recorded per mode in Results).
- [x] The `embed` stage is registered; changing the model changes the config fingerprint.
- [x] Vector search for one snapshot never returns another snapshot's chunks (integration test).
- [x] Hybrid retrieval on a pinned mid-size repo completes in under 1 second (warm), recorded in Results.

## Verification

```bash
cd backend
uv run pytest tests/unit/retrieval tests/integration/retrieval -q
uv run repoatlas build https://github.com/<owner>/<repo>        # now includes chunk and embed stages
uv run repoatlas search --snapshot <id> "where are routes registered" --mode hybrid --explain
```

## Risks and open questions

- CPU embedding time for large repos; caching by chunk hash makes rebuilds cheap, and caps bound first builds.
- The default model's quality on Python vs TypeScript may differ; recorded per language in Results.
- Open: whether a local cross-encoder reranker beats score fusion; it can be tried later behind the same `Retriever` interface.

## Results

Gate checked on 2026-11-11 on branch `m06-retrieval`.

**Tests.** In `backend/`: `uv run poe check` passes on Windows: 938 passed, 1 skipped (creating symlinks needs extra privileges on Windows); ruff, mypy (199 files), and the docs lint pass. In `frontend/` (untouched, but its real-API Playwright project now builds through `chunk` and `embed`): lint, typecheck, 239 unit tests, and 7/7 Playwright tests pass. `repoatlas api openapi --check`: the spec is unchanged.

**Exit criteria**
- *Unit tests pass for chunk boundaries, identifier splitting, rank fusion, and graph expansion.* `tests/unit/retrieval/`: `test_chunker.py` (spans, class and module chunks, collapsed imports, splitting at statement boundaries with a 10-line overlap, headers from the chunk's own file), `test_keyword.py` (13 identifier cases, stems, tsvector literals, the query builder), `test_fusion.py`, `test_expansion.py` (hop decay, confidence products, a strong two-hop path beating a weak edge, deduplication across seeds), plus `test_rerank.py`, `test_embed.py`, and `test_evidence.py`.
- *With the real embedding model, at least 80% of fixture queries find an expected symbol in the top 5 in hybrid mode.* `test_fixture_queries.py` over 24 labeled queries (20 natural-language, 4 identifiers; 12 Python, 12 TypeScript/JavaScript), top 5:

  | Model | Mode | All | py_small | ts_small | Natural | Identifier |
  |---|---|---|---|---|---|---|
  | `jinaai/jina-embeddings-v2-base-code` | hybrid | **100%** | 100% | 100% | 100% | 100% |
  | | vector | 100% | 100% | 100% | 100% | 100% |
  | | keyword | 83% | 83% | 83% | 80% | 100% |
  | `hash-256` (CI) | hybrid | 88% | 83% | 92% | 85% | 100% |
  | | vector | 79% | 67% | 92% | 75% | 100% |
  | | keyword | 83% | 83% | 83% | 80% | 100% |

  Keyword search misses `py-cart-total`, `py-routes`, `ts-clear`, and `ts-get-cart` (no shared words; for example "remove every item" for `clear`). The fixture repos are tiny (about 60 chunks each), so 100% shows the pipeline works end to end, not that the model is strong; Python and TypeScript did equally well here. A real comparison belongs to the evaluation work. CI asserts hybrid of at least 70% with the hash embedder, every identifier query found, and hybrid at least as good as keyword alone.
- *The `embed` stage is registered; changing the model changes the config fingerprint.* `test_the_embed_stage_follows_chunk_and_precedes_publish` and `test_changing_the_model_changes_the_fingerprint` (`embedding_model` is the stage's setting; `embedding_threads` does not count).
- *Vector search for one snapshot never returns another snapshot's chunks.* `tests/integration/retrieval/test_vector.py::test_vector_search_never_returns_another_snapshots_chunks`: three snapshots, two of them with identical content and so the very same cached vectors; asking each for every chunk returns exactly its own count and only its own chunks.
- *Hybrid retrieval on a pinned mid-size repo completes in under 1 second (warm).* `colinhacks/zod` @ `19c40d2` (522 files, 5,587 nodes, 17,836 edges, 5,645 chunks), snapshot 13 in the local database, the real model, 10 queries x 3 rounds per mode in one process after a warm-up query:

  | Mode | Median | p95 | Max | Median per step |
  |---|---|---|---|---|
  | hybrid | **239 ms** | 327 ms | 358 ms | vector 107, keyword 21, load 7, fusion 0.5, expansion 46, rerank 13 ms |
  | vector | 135 ms | 215 ms | 278 ms | vector 122 ms (query embedding and exact search) |
  | keyword | 32 ms | 47 ms | 99 ms | keyword 21 ms |

  The first query in a process took 7.6 s (loading the model and the graph), so one `repoatlas search` takes about 3 s; the exact vector step stays under the 300 ms target.

**Building with the real model.** zod's build took 1,858 s, of which embedding took 1,843 s for 5,320 new chunks (about 3 chunks per second on this CPU, peak memory about 4.1 GB); the other stages took 15 s. A rebuild of the same chunks embeds nothing (320 vectors left by the interrupted first attempt were reused). First builds of large repos are slow on CPU, as the risks section expected; the backlog has an entry for faster embedding.

**Deviations from the design notes**
- Readiness moved to a final `publish` stage (decision 0016), so a failed `chunk` or `embed` never leaves a ready snapshot without chunks or vectors. `store` is version 2; `save_snapshot(ready=True)` stays the default for direct callers, and saving again deletes the snapshot's chunks too.
- `tsv` is written as an exact `tsvector` literal (header terms at weight A, body at D), not by `to_tsvector`, because Postgres's parser splits identifiers its own way. Each identifier keeps its raw form and its split, lightly stemmed words (`routes` to `route`). Queries are an `or` of the split, stemmed, non-stopword terms (plus a camelCase identifier whole) for `websearch_to_tsquery('simple', ...)`; an `and` of every word found almost nothing for questions.
- The exact-name bonus is 1/60 (about one first place in a fused list). A plain identifier matches a symbol's name; a dotted one (`Cart.add`) matches its qualified name, so `GiftCart.add` gets no bonus.
- Packing skips chunks inside an already selected function, method, or test only: class chunks leave member bodies out and module chunks skip symbols, so they never stand in for code in their range.
- Class chunks keep each member's lines up to its signature (decorators included) and mark the body with `...`, keeping a lone closing brace. Module chunks drop runs of blank lines left by removed symbols; a long module chunk splits into parts whose ranges come from their own lines.
- `EvidenceRef.from_compact` without a full OID keeps the 7-digit prefix (`abbreviated` is then true); with one, it checks the prefix and keeps the full OID.
- Embedding: model batches are grouped by an estimated token budget, longest first, and documents are truncated at 2,048 tokens (`embed` version 2); see below.

**Found and fixed at the gate**
- The first real build (zod) grew to 10.7 GB and stalled in `embed`: fastembed pads each batch of 64 to its longest text, and attention memory grows with batch size times length squared. Fixed in `b398774`: chunks are embedded longest first, model batches stay within 8,192 estimated tokens, and documents are truncated at 2,048 tokens. The same build then peaked at about 4.1 GB.
- `test_build_follow_and_read_a_repository_over_http` (M04) failed again in one full run, as at the M05 gate: it polled only after starting the worker. It now polls once first (`2e2136a`).

**CI.** Run [43075079131](https://github.com/rajatsxngh/RepoAtlas/actions/runs/43075079131) on `3df6d98` passed all five jobs: the Linux full check (939 passed, with the symlink test and every Postgres suite, using the hash embedder, so no model download), the Windows unit tests (686 passed), the API types job, the docs lint, and the frontend job (lint, typecheck, unit tests, and all 7 Playwright tests, whose real-API builds now run `chunk` and `embed`).

**Left as is.** The local development database still has snapshot 12 (zod, `building`), left by the build that was stopped before the memory fix; its fingerprint is no longer requested, so nothing reuses it.
