# M06 — Retrieval

**Type of work:** Search · **Size:** L (10 tasks) · **Status:** Not started

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
- [ ] **T3 — Embedders and `embed` stage.** `FastEmbedEmbedder`, `HashEmbedder`, cache lookups, fingerprint includes the model; tests with `HashEmbedder`.
- [ ] **T4 — Keyword index.** Identifier splitting (table-driven tests), `tsvector`, query builder, ranking.
- [ ] **T5 — Vector search.** Exact per-snapshot search; integration test that results never leak across snapshots.
- [ ] **T6 — Rank fusion and seeds.** Tests with synthetic rankings.
- [ ] **T7 — Graph expansion.** Tests on the fixture graph (hop decay, confidence weighting, deduplication).
- [ ] **T8 — Rerank and budget packing.**
- [ ] **T9 — Search CLI.** `--explain` output with per-step scores.
- [ ] **T10 — Retrieval checks and latency.** Fixture queries reach their expected symbols in the top k (with `HashEmbedder` for keyword/graph paths in CI, and the real model locally); latency on a pinned mid-size repo recorded in Results.

## Exit criteria

- [ ] Unit tests pass for chunk boundaries, identifier splitting, rank fusion, and graph expansion.
- [ ] With the real embedding model, at least 80% of fixture queries find an expected symbol in the top 5 in hybrid mode (recorded per mode in Results).
- [ ] The `embed` stage is registered; changing the model changes the config fingerprint.
- [ ] Vector search for one snapshot never returns another snapshot's chunks (integration test).
- [ ] Hybrid retrieval on a pinned mid-size repo completes in under 1 second (warm), recorded in Results.

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

_Filled in at the gate._
