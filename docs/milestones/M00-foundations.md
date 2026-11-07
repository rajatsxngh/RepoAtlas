# M00 — Foundations

**Type of work:** Setup and infrastructure · **Size:** M (8 tasks) · **Status:** In progress

## Goal

A clean, reproducible monorepo where any later work can add code with tests, database migrations, and CI from the first commit — on Windows for development and Linux for CI and deployment.

## Depends on

None.

## Provides

- **Repository layout** (see PRD §6):
  ```
  backend/            Python package `repoatlas` (src layout)
    pyproject.toml, uv.lock, .python-version
    src/repoatlas/    __init__.py, config.py, logging.py, cli.py, db/ (base, engine, session, migrate)
    src/repoatlas/db/migrations/   Alembic env.py, script template, versions/ (shipped in the package)
    tests/            unit/, integration/, conftest.py
  frontend/           placeholder README for the web app
  eval/  sandbox/     placeholder READMEs
  infra/              docker-compose.yml
  docs/  scripts/
  ```
- **Settings:** `repoatlas.config.Settings` (pydantic-settings, env prefix `REPOATLAS_`, reads `.env`). Fields: `database_url`, `log_level`, `log_format` (`console`|`json`), `github_token` (optional), `anthropic_api_key` (optional, unused so far), `data_dir`.
- **Logging:** `repoatlas.logging.configure(settings)` using structlog (console locally, JSON in CI and deployment).
- **Database:** `repoatlas.db.engine.get_engine()` / `create_db_engine(url)` / `redact_url(url)`, `repoatlas.db.session.session_scope()`, SQLAlchemy 2 `Base` with a constraint naming convention. Migrations ship inside the package (`repoatlas.db:migrations`) and run programmatically through `repoatlas.db.migrate` (`upgrade`, `current_revision`, `head_revision`, `create_revision`) — no `alembic.ini`, so they work the same from a checkout, a wheel, or a container.
- **Baseline migration** `0001_baseline`: `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;`
- **CLI:** `repoatlas --version`, `repoatlas db upgrade`, `repoatlas db current`, `repoatlas db revision "<message>" [--autogenerate]` (Typer app in `repoatlas.cli`; subcommands register through `app.add_typer`). An unreachable database gives a short error with the password redacted and the command to start Postgres.
- **Test infrastructure:** pytest markers `unit` and `integration` (applied automatically by directory) and `live` (opt-in with `-m live`; never run in CI). Integration fixtures: `pg_url` (a throwaway database created next to the configured one, migrated to head, dropped after the session; admin URL from `REPOATLAS_TEST_DATABASE_URL` or settings), `pg_engine`, and `db_session` (changes rolled back after each test). Unit tests build settings with `Settings(_env_file=None)` so a developer's `.env` never leaks in.
- **Tasks (poethepoet):** `lint`, `fmt`, `typecheck`, `test` (excludes `live`), `test-unit`, `docs-lint`, `check` (lint + typecheck + test + docs lint).
- **CI** (`.github/workflows/ci.yml`): `backend-linux` (Postgres service `pgvector/pgvector:pg16`, runs `poe check`), `backend-windows` (unit tests only), `docs` (docs lint). Later jobs are added to this workflow.
- **Pre-commit:** trailing whitespace, end-of-file, merge-conflict, YAML, TOML, and large-file checks; ruff check and format (run from the backend environment so the version matches `uv.lock`); docs lint.
- **`CLAUDE.md`** with real commands filled in.

## Requirements

FND-1, FND-2, FND-3, FND-4, FND-5, FND-6, FND-7, FND-8

## Scope

**In:** tooling, settings, logging, database baseline, CLI skeleton, test fixtures, CI, pre-commit, docs wiring.

**Out:** any product code (fetching, parsing, graphs, API, UI).

## Design notes

- **Python version:** 3.12, pinned with `.python-version` and `requires-python = ">=3.12,<3.13"`. uv downloads it; the system Python 3.11 is not used.
- **Dependencies (initial):** pydantic, pydantic-settings, sqlalchemy (2.x), psycopg[binary] (3.x), alembic, typer, structlog. Dev: pytest, pytest-cov, ruff, mypy, poethepoet, pre-commit.
- **mypy:** strict for `src/repoatlas`; tests type-checked leniently.
- **ruff:** line length 100; rule sets E, F, W, I, B, UP, SIM, RUF. Repo-level scripts use the root `ruff.toml` (same rules, Python 3.11 target).
- **Postgres:** image `pgvector/pgvector:pg16`, host port **5433** (avoids clashing with a local Postgres), named volume, healthcheck `pg_isready`. Test runs create and drop a database named `repoatlas_test_<random>`.
- **Line endings:** `.gitattributes` with `* text=auto eol=lf` plus `binary` for images and archives; contributors set `git config core.autocrlf false`.
- **Windows:** all poe tasks are plain commands (no shell-specific syntax), so they work in PowerShell and Git Bash.
- **Secrets:** only in `.env` (gitignored). `.env.example` lists every variable with a comment.
- **Docs lint:** `scripts/check_milestones.py` is stdlib-only and runs with any Python 3.11+.
- **License:** MIT (chosen by the owner).

## Tasks

- [x] **T1 — Initialize the repo.** `git init`; `.gitignore` (Python, Node, `.env`, `.venv`, caches, large eval artifacts), `.gitattributes`, `.editorconfig`, LICENSE (after the owner picks one), README stub linking to `docs/`.
- [x] **T2 — Prerequisites (owner).** Install Docker Desktop with the WSL2 backend; confirm `docker compose version`. Create `.env` from `.env.example`; optionally add `REPOATLAS_GITHUB_TOKEN` (raises the GitHub API limit from 60 requests per hour) and `REPOATLAS_ANTHROPIC_API_KEY`.
- [x] **T3 — Backend project.** `uv init --package` in `backend/`; dependencies above; ruff, mypy, and pytest config in `pyproject.toml`; poe tasks.
- [x] **T4 — Local database.** `infra/docker-compose.yml` with the `db` service; `.env.example` with `REPOATLAS_DATABASE_URL=postgresql+psycopg://repoatlas:repoatlas@localhost:5433/repoatlas`.
- [x] **T5 — Settings, logging, database, migrations.** Implement the modules above; Alembic init; `0001_baseline`; `repoatlas db upgrade` and `db current`.
- [x] **T6 — Test infrastructure.** Markers, fixtures, and smoke tests: settings load from env, the database is reachable, both extensions exist after upgrade.
- [x] **T7 — CI and pre-commit.** Workflow with the three jobs; pre-commit config; docs lint wired into both.
- [x] **T8 — Session bootstrap.** Fill in `CLAUDE.md` commands with the real ones; verify every docs link resolves.

## Exit criteria

- [x] `docker compose -f infra/docker-compose.yml up -d` then `uv run repoatlas db upgrade` succeeds, and `uv run repoatlas db current` shows `0001_baseline`.
- [x] `uv run poe check` passes locally on Windows.
- [x] `python scripts/check_milestones.py` passes.
- [ ] CI passes on Linux and Windows after the first push. (Creating the GitHub repo and pushing happen only after the owner approves.)

## Verification

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend && uv sync && uv run repoatlas db upgrade && uv run repoatlas db current
uv run poe check
python ../scripts/check_milestones.py
```

Manual: open the CI run and confirm all three jobs are green.

## Risks and open questions

- Docker Desktop and WSL2 setup on Windows can take a few tries (virtualization must be enabled in BIOS).
- Port 5433 might already be in use; set `REPOATLAS_DB_PORT` in your shell before `docker compose up`, and point `REPOATLAS_DATABASE_URL` at the same port.

## Results

_Local checks recorded 2026-11-07. The gate closes once CI passes on the first push._

| Check | Evidence |
|---|---|
| Local Postgres | `pgvector/pgvector:pg16` (digest `sha256:0dd3149a06e4…`), healthy on port 5433; extensions `vector` 0.8.7 and `pg_trgm` 1.6 |
| Migrations | `repoatlas db upgrade` reaches `0001_baseline`; a second upgrade is a no-op; `repoatlas db current` prints `0001_baseline` |
| `poe check` on Windows | ruff and format clean; mypy clean (21 files); 36 tests passed (28 unit, 8 integration) in about 7 s; docs lint OK |
| Test isolation | No `repoatlas_test_*` databases remain after the run |
| Packaging | The built wheel contains `repoatlas/db/migrations/` (env, template, versions) and `py.typed` |
| Toolchain | Python 3.12.13 via uv 0.12.3; Docker 29.9.2, Compose v5.6.1, Docker Desktop 4.98 on WSL 2 |
| CI on Linux and Windows | Pending: the first push needs the owner's OK |
