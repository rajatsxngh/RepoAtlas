# 0012 — Hardened Docker sandbox for benchmark repos only

**Status:** Accepted · **Date:** 2026-11-07

## Context

Real impact ground truth and SCIP indexes require installing dependencies and running test suites — executing third-party code. Docker Desktop on Windows runs containers in a shared WSL2 VM, and stronger isolation features require paid plans, so containers are a defense layer rather than a guarantee.

## Decision

- Only curated, pinned benchmark (and calibration) repos ever run in the sandbox. User-submitted repos are never executed.
- Test runs use `--network none`, `--cap-drop ALL`, `no-new-privileges`, a non-root user, a read-only root filesystem with a capped tmpfs, pids/memory/CPU limits, a host-side wall-clock kill, no bind mounts, and no Docker socket — applied in one tested function.
- Dependency installs happen during image builds on an internal network whose only exit is an allowlist proxy for package registries; the Docker host and its services are unreachable.
- Installs are pinned (lockfiles, or resolution limited to the commit's date), and image digests are recorded.

## Consequences

- Real-failure ground truth and SCIP indexes can be produced safely and reproducibly.
- RepoAtlas has no "run my repo's tests" feature in v1.
- Repos whose tests need services or network access are excluded from the benchmark.
