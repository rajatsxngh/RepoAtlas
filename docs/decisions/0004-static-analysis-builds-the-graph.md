# 0004 — Static analysis builds the graph

**Status:** Accepted · **Date:** 2026-11-07

## Context

Graphs whose edges are written by an LLM are expensive to build, vary between runs, fail schema validation, and can't be checked. RepoAtlas's value depends on answers that can be verified against code.

## Decision

- Only code creates nodes and edges: tree-sitter extraction plus deterministic resolvers.
- The LLM never writes graph structure. It only answers questions using evidence.
- Every edge records its provenance (`tree_sitter`, `import_resolver`, `name_match`, `convention`, `git`), a confidence in (0, 1], and, for calls and imports, the exact source range.
- `build_graph` is a pure, deterministic function, golden-tested on fixture repos.

## Consequences

- Builds are cheap, reproducible, and testable; a graph can be diffed between commits.
- Graph quality is bounded by static analysis (dynamic dispatch, metaprogramming); confidence values and unresolved-call records make that visible instead of hiding it.
