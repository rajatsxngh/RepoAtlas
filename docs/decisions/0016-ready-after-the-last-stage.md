# 0016 — A snapshot is ready only after its last stage

**Status:** Accepted · **Date:** 2026-11-11 · **Amends:** 0007 (stages)

## Context

Retrieval adds stages that work on the stored snapshot: `chunk` reads file text from the store's `blobs` table and needs node rows, and `embed` needs the chunks. They must run after `store`. But `store` (through `save_snapshot`) marked the snapshot `ready`, and a ready snapshot is reused by every later build request. If `chunk` or `embed` failed, the snapshot stayed ready without chunks or embeddings, and no build would ever fix it.

## Decision

- `save_snapshot` takes `ready` (default `True`, so direct callers are unchanged). The `store` stage saves with `ready=False`: the snapshot stays `building` with its rows and stats written. Saving again replaces the snapshot's rows, chunks included.
- A new built-in stage, `publish`, runs last and calls `repoatlas.store.loader.mark_ready`, which makes the snapshot `ready` and merges the stats later stages left in `BuildContext.stats` (and the final timings) into `stats`.
- Stages that work on the stored snapshot register between `store` and `publish` (`register_stage(stage, after="store")`). The modules listed in `BUILTIN_STAGE_MODULES` register with `register_builtin`, which does not trigger loading the built-ins again.
- `store` is now version 2, so the fingerprint changes and earlier snapshots are not reused (as after any pipeline change).

## Consequences

- A failure in any stage leaves the snapshot `building` or `failed`, never ready; a retry runs the remaining stages (and redoes `store`, which is idempotent).
- Readers that need a ready snapshot wait until the whole build has finished, as before.
- One more stage in the progress bar, with a tiny weight.
