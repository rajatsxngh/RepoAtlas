# M00 — Foundations

**Type of work:** Setup and infrastructure · **Size:** M (8 tasks) · **Status:** Not started

## Goal

A clean, reproducible monorepo where any later work can add code with tests, database migrations, and CI from the first commit — on Windows for development and Linux for CI and deployment.

## Depends on

None.

## Provides

- **Repository layout** (see PRD §6):
  ```
  backend/            Python package `repoatlas` (src layout)
    pyproject.toml, uv.lock, .python-version
    src/repoatlas/    __init__.py, config.py, logging.py, cli.py, db/
    alembic/          migrations (env.py wired to settings)
    tests/            unit/, integration/, conftest.py
  frontend/           placeholder README for the web app
  eval/  sandbox/     placeholder READMEs
  infra/              docker-compose.yml
  docs/  scripts/
  ```
- **Settings:** `repoatlas.config.Settings` (pydantic-settings, env prefix `REPOATLAS_`, reads `.env`). Fields: `database_url`, `log_level`, `log_format` (`console`|`json`), `github_token` (optional), `anthropic_api_key` (optional, unused so far), `data_dir`.
- **Logging:** `repoatlas.logging.configure(settings)` using structlog (console locally, JSON in CI and deployment).
- **Database:** `repoatlas.db.engine.get_engine()`, `repoatlas.db.session.session_scope()`, SQLAlchemy 2 `Base`; Alembic environment reading `database_url` from settings.
- **Baseline migration** `0001_baseline`: `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;`
- **CLI:** `repoatlas --version`, `repoatlas db upgrade`, `repoatlas db current` (Typer app in `repoatlas.cli`; subcommands register through `app.add_typer`).
- **Test infrastructure:** pytest markers `unit`, `integration` (needs Postgres), `live` (needs API keys; never run in CI); fixtures `settings`, `pg_engine` (creates a throwaway database per test session), `db_session` (transaction rolled back after each test).
- **Tasks (poethepoet):** `lint`, `fmt`, `typecheck`, `test` (excludes `live`), `test-unit`, `check` (lint + typecheck + test), `docs-lint`.
- **CI** (`.github/workflows/ci.yml`): `backend-linux` (Postgres service `pgvector/pgvector:pg16`, runs `poe check`), `backend-windows` (unit tests only), `docs` (docs lint). Later jobs are added to this workflow.
- **Pre-commit:** ruff, ruff-format, trailing whitespace, end-of-file, merge-conflict check, docs lint.
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
- **ruff:** line length 100; rule sets E, F, I, B, UP, SIM, RUF.
- **Postgres:** image `pgvector/pgvector:pg16`, host port **5433** (avoids clashing with a local Postgres), named volume, healthcheck `pg_isready`. Test runs create and drop a database named `repoatlas_test_<random>`.
- **Line endings:** `.gitattributes` with `* text=auto eol=lf` plus `binary` for images and archives; contributors set `git config core.autocrlf false`.
- **Windows:** all poe tasks are plain commands (no shell-specific syntax), so they work in PowerShell and Git Bash.
- **Secrets:** only in `.env` (gitignored). `.env.example` lists every variable with a comment.
- **Docs lint:** `scripts/check_milestones.py` is stdlib-only and runs with any Python 3.11+.
- **License:** chosen by the owner before the first public push (MIT suggested).

## Tasks

- [ ] **T1 — Initialize the repo.** `git init`; `.gitignore` (Python, Node, `.env`, `.venv`, caches, large eval artifacts), `.gitattributes`, `.editorconfig`, LICENSE (after the owner picks one), README stub linking to `docs/`.
- [ ] **T2 — Prerequisites (owner).** Install Docker Desktop with the WSL2 backend; confirm `docker compose version`. Create `.env` from `.env.example`; optionally add `REPOATLAS_GITHUB_TOKEN` (raises the GitHub API limit from 60 requests per hour) and `REPOATLAS_ANTHROPIC_API_KEY`.
- [ ] **T3 — Backend project.** `uv init --package` in `backend/`; dependencies above; ruff, mypy, and pytest config in `pyproject.toml`; poe tasks.
- [ ] **T4 — Local database.** `infra/docker-compose.yml` with the `db` service; `.env.example` with `REPOATLAS_DATABASE_URL=postgresql+psycopg://repoatlas:repoatlas@localhost:5433/repoatlas`.
- [ ] **T5 — Settings, logging, database, migrations.** Implement the modules above; Alembic init; `0001_baseline`; `repoatlas db upgrade` and `db current`.
- [ ] **T6 — Test infrastructure.** Markers, fixtures, and smoke tests: settings load from env, the database is reachable, both extensions exist after upgrade.
- [ ] **T7 — CI and pre-commit.** Workflow with the three jobs; pre-commit config; docs lint wired into both.
- [ ] **T8 — Session bootstrap.** Fill in `CLAUDE.md` commands with the real ones; verify every docs link resolves.

## Exit criteria

- [ ] `docker compose -f infra/docker-compose.yml up -d` then `uv run repoatlas db upgrade` succeeds, and `uv run repoatlas db current` shows `0001_baseline`.
- [ ] `uv run poe check` passes locally on Windows.
- [ ] `python scripts/check_milestones.py` passes.
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
- Port 5433 might already be in use; it is configurable through `.env`.
- Open: which license to use.

## Results

_Filled in at the gate._
