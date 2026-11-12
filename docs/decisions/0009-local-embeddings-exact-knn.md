# 0009 — Local embeddings with fastembed; exact per-snapshot search

**Status:** Accepted · **Date:** 2026-11-07

## Context

Embedding APIs add cost, a second API key, and a dependency for self-hosters. Approximate vector indexes in pgvector filter rows after their scan, so filtering by snapshot can return too few or skewed results when many snapshots share the table.

## Decision

- Embeddings run locally on CPU through fastembed. The provisional default model is `jinaai/jina-embeddings-v2-base-code` (768 dimensions); a comparison in the evaluation work confirms or replaces it.
- Embeddings are cached in an `embeddings` table keyed by (chunk hash, model) with an untyped `vector` column, shared across snapshots.
- Vector search is exact within one snapshot (filter first, then order by distance); no approximate index in v1.
- The embedding model is part of the config fingerprint.

## Consequences

- No embedding API cost; self-hosting needs no extra keys.
- First builds are slower on CPU, and the model is a large one-time download; caching makes rebuilds cheap.
- Exact search scales to the repo size caps; very large deployments may need approximate indexes later (backlog).

## Addendum

_Embedding comparison result goes here._

## Addendum — embedding comparison (2026-11-12)

The default model was compared with BAAI/bge-small-en-v1.5 on the dev split and kept: see decision 0018.
