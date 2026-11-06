# 0003 — Stable symbol keys

**Status:** Accepted · **Date:** 2026-11-07

## Context

Benchmark ground truth, caches, incremental builds, URLs, and test-to-symbol mappings all need to name the same function across snapshots and commits. Database row IDs change with every snapshot, and line numbers change with unrelated edits.

## Decision

- Every symbol has a key `<lang>:<path>:<qualname>:<kind>[~<n>]` (full grammar in PRD §7.3): no line numbers; same-named siblings disambiguated by source order; anonymous functions as `<anon>`; test cases by their title path; `:`, `~`, and `%` percent-encoded inside parts.
- Directories, files, and routes use `dir:<path>`, `file:<path>`, and `route:<path>:<METHOD> <pattern>`.
- Row IDs stay internal to the store; every interface between modules, and every API, uses symbol keys.
- Each symbol stores both its name range and its full span.

## Consequences

- Ground truth and system output compare by key; caches and incremental builds reuse work by key.
- Moving or renaming a file changes its symbols' keys (treated as delete plus add).
- Reordering same-named siblings changes their `~n` suffixes; rare in practice, and visible in diffs.
