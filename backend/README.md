# RepoAtlas backend

Python package `repoatlas`: ingestion, code graph, storage, API, retrieval, answers, and evaluation tooling.

```bash
uv sync                      # create the virtualenv and install dependencies
uv run repoatlas --help      # the CLI
uv run poe check             # lint, format check, type check, tests, docs lint
uv run poe test-unit         # unit tests only (no Postgres needed)
```

Integration tests need the local Postgres from `infra/docker-compose.yml`. Settings come from environment variables prefixed `REPOATLAS_` or from the nearest `.env` file (see `.env.example` at the repo root).
