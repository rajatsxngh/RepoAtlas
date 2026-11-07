# 0007 — Postgres job queue, stage registry, config fingerprint

**Status:** Accepted · **Date:** 2026-11-07

## Context

Builds take minutes and must survive worker crashes. New pipeline steps (chunking, embeddings, and later work) keep getting added. A snapshot built with an older pipeline must never be silently mixed with output from a newer one.

## Decision

- Jobs live in a Postgres `jobs` table, claimed with `SELECT … FOR UPDATE SKIP LOCKED`, with leases, heartbeats, retries with backoff, and a separate worker process. No Redis.
- Build steps are stages in a registry (`name`, `version`, relevant settings keys, `run`). New stages register themselves; the job runner doesn't change.
- The config fingerprint is a hash of the ordered stages, their versions, and their settings. A snapshot's identity is (repo, commit, fingerprint).
- Stages are idempotent so a retried job resumes instead of starting over.

## Consequences

- Crash-safe builds with visible progress and no extra infrastructure.
- Changing any stage produces new snapshots instead of inconsistent old ones.
- Queue throughput is limited compared with a dedicated broker; ample for this workload.
- Rate limits are in-process (one API instance in v1).

## Addendum (2026-11-07): the build steps became registered stages

The synchronous build added with the graph store had its own step list (`BUILD_STEPS`) and computed the fingerprint over it. A second list for the worker would give `repoatlas build` and background builds different fingerprints for the same build, so there is one pipeline:

- `repoatlas.pipeline.stages` holds the registry. `fetch`, `inventory`, `parse`, `graph`, and `store` are its built-in stages, each with a version and a weight; the step functions are unchanged apart from checkpoints. `resolve` is no longer a step: it is the request-time work (validate the URL, check GitHub, pin the commit, compute the fingerprint) that both `run_build` and a build request do before any stage runs.
- `run_build(…, stages=None)` and `repoatlas build` run the registered stages in process. `BUILD_STEPS`, `Step`, and the `steps` parameter are gone; `fingerprint_parts` and `config_fingerprint` live in `repoatlas.pipeline.stages`.
- Snapshots stored before this change carry the old fingerprint. They stay readable, but builds no longer reuse them: the next build of the same commit creates a new snapshot, as after any pipeline change.
- Stages keep checkpoints (the fetched commit, and the inventory, parse results, and graph as JSON) in a work directory named after a digest of (repo, commit, fingerprint) under `data_dir/builds`, so a retried build resumes where the last attempt stopped. Run on another machine, a retry simply starts over.
