# 0006 — One Postgres; in-memory traversal per snapshot

**Status:** Accepted · **Date:** 2026-11-07

## Context

RepoAtlas stores graphs, file text, chunks, embeddings, jobs, and traces. A separate graph database and vector store would add services to run and self-host. Recursive SQL on cyclic call graphs explodes in the number of paths it explores.

## Decision

- One Postgres 16 database with `pgvector` and `pg_trgm` holds everything.
- Graph traversals (callers, callees, paths, impact) run on an in-memory adjacency built per snapshot and kept in an LRU cache with a memory cap; breadth-first and best-first searches use visited sets.
- SQL is used for lookups, filtering, full-text and vector search, and aggregation inputs.

## Consequences

- One database to run, back up, and self-host.
- Traversals are fast and cycle-safe; memory per snapshot is bounded by the repo size caps.
- A cold snapshot pays a one-time load (target under 2 seconds for a mid-size repo).
