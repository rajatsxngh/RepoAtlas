# 0002 — Source bytes from git objects; blob OIDs; file text in Postgres

**Status:** Accepted · **Date:** 2026-11-07

## Context

- A checked-out working tree on Windows can differ from the repository: line endings may be converted, file names that NTFS rejects fail to check out, case-only differences collide, and symlinks behave differently. Hashes and line ranges computed from such a tree would differ from Linux.
- The API and the worker deploy as separate services, so the API can't read files from the worker's disk.
- Many snapshots of the same repo share most files.

## Decision

- File bytes are read from git objects or the commit's source archive, never from a checked-out working tree. Git runs with `core.autocrlf=false`, `GIT_LFS_SKIP_SMUDGE=1`, https only, and no submodule recursion.
- A file's content hash is its git blob OID.
- File text is stored in a Postgres `blobs` table keyed by OID; every code read (code viewer, citation validation, agent tools) goes through it.
- Symlinks, submodules, and LFS pointers are skipped with a recorded reason.
- The exact fetch mechanism (depth-1 clone without checkout vs streamed source archive) is chosen by a spike in the ingestion milestone and recorded below as an addendum.

## Consequences

- Inventory, parse, and graph digests are identical on Windows and Linux (checked in CI).
- The API never needs a checkout; any instance can serve code.
- Blobs are deduplicated across snapshots, which also makes incremental rebuilds cheap.
- Postgres stores source text (bounded by size caps).

## Addendum

_Fetch-spike result goes here._
