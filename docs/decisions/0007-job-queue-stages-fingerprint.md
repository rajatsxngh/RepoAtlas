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
