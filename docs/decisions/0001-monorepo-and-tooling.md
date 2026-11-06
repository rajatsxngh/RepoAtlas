# 0001 — Monorepo layout and tooling

**Status:** Accepted · **Date:** 2026-11-07

## Context

One developer builds RepoAtlas with a coding assistant. Development happens on Windows 11; CI and deployment run on Linux. The project has a Python backend, a TypeScript web app, evaluation tooling, sandbox images, and documentation that every coding session reads.

## Decision

- One repository: `backend/`, `frontend/`, `eval/`, `sandbox/`, `infra/`, `docs/`, `scripts/`.
- Backend: Python 3.12 managed by uv (`uv.lock` committed), ruff, mypy (strict on `src/`), pytest, and poethepoet tasks — plain commands that work in PowerShell and Git Bash.
- Frontend: Next.js with npm (`package-lock.json` committed). pnpm isn't needed for a single app.
- LF line endings enforced with `.gitattributes`.
- CI on GitHub Actions: Linux runs everything that isn't a live test; Windows runs unit tests and the cross-OS determinism check.
- Docs lint (`scripts/check_milestones.py`) uses only the Python standard library.

## Consequences

- Docs, code, and contracts change together in one place and one history.
- Windows CI catches path and line-ending problems before they reach Linux.
- Tooling stays small: no Makefile or shell scripts that only work on one OS.
