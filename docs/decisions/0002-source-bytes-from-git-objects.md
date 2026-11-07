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

## Addendum — fetch spike (2026-11-07)

Measured on `pallets/flask` at `49943b5` (236 files, 1.8 MB of source) from this Windows machine:

| | Depth-1 git fetch + `ls-tree` / `cat-file --batch` | GitHub source tarball streamed in memory |
|---|---|---|
| Time | 2.0 s | 0.6 s |
| Blob IDs | Reported by git; 236 of 236 match the bytes read | 236 of 236 match after hashing locally |
| Modes, symlinks, submodules | All reported by `ls-tree` | Symlinks only; submodules absent; modes need a separate tree listing |
| Hosts and testing | Any git remote; offline tests through `file://` repositories | GitHub only |

**Decision: depth-1 git fetch.** RepoAtlas initializes a bare repository, runs `git fetch --depth=1 --no-tags <url> <sha>`, lists files with `git ls-tree -r -l -z --full-tree <sha>`, and reads blobs through a single `git cat-file --batch` process. No working tree is ever written. The 1.4 s difference is small next to parsing and embedding, and this option gets modes and IDs from git itself, works with any remote, and can be tested offline.

**History:** a second bare repository, configured as a partial clone (`extensions.partialClone=origin`, `remote.origin.promisor=true`, `remote.origin.partialclonefilter=blob:none`), fetches `--depth=1000 --filter=blob:none` of the same commit: 5,417 commits in 2.9 s with zero blobs (depth counts generations, and merges add more than one commit per generation). `git log --no-renames --name-only` over 1,000 commits still downloads zero blobs (0.2 s), but git's default rename detection silently downloads blobs (25 blobs for 300 commits, 2.4 s). **History queries must always pass `--no-renames`.**

**Git runs hermetically:** `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<null device>`, `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, `GIT_LFS_SKIP_SMUDGE=1`, and `-c credential.helper= -c core.autocrlf=false -c core.symlinks=false`. Machine-level settings — this development machine's system config sets `autocrlf=true` and a credential helper that waits on a GUI window — can't change bytes or block on a prompt.
