# Deploying RepoAtlas

How the pieces fit on a hosted deployment, and every environment variable of each service.
Self-hosting with Docker Compose is in [self-hosting.md](self-hosting.md); operations are in
the [runbook](runbook.md).

> **Status: built for, not deployed.** Hosting costs money, and the owner has deferred all
> spending until the build is finished (decision
> [0020](decisions/0020-build-without-spend-live-pass.md)). Nothing below has been run on a
> hosting provider yet. The steps are the [live pass](live-pass.md); where a provider's
> commands are named, confirm them on the provider's site first.

## The pieces

| Piece | Runs | Image or build | Needs |
|---|---|---|---|
| Postgres | Managed by the backend provider | Postgres 16 with the `vector` and `pg_trgm` extensions | Backups switched on |
| API | Railway or Fly (one instance) | `infra/docker/api.Dockerfile` | About 1.5 GB of memory (it holds the embedding model for questions and a graph cache) |
| Worker | Railway or Fly (one instance; more are safe) | `infra/docker/worker.Dockerfile` | At least 2 GB of memory for CPU embeddings; outbound access to GitHub; a volume or disk at `/data` (optional: build checkpoints) |
| Web app | Vercel | `frontend/` (Next.js) | The API's address and the access token |

The browser talks only to the web app. The web app's server forwards `/api/v1/*` to the API
(`REPOATLAS_API_ORIGIN`) and adds the access token, so the API needs no CORS and can stay on a
private or unlisted address.

Run **one** API instance: per-visitor rate limits and daily caps are counted in its memory. The
daily spend cap is read from the database and holds across instances and restarts.

## Environment variables

### API and worker (backend)

Both read the same settings (`backend/src/repoatlas/config.py`); every name has the
`REPOATLAS_` prefix. Set them in the provider's secret manager. `.env` files are never
deployed.

| Variable | API | Worker | Value |
|---|---|---|---|
| `REPOATLAS_DATABASE_URL` | required | required | The managed Postgres URL (`postgres://…` is accepted). **Secret.** |
| `REPOATLAS_ACCESS_TOKEN` | required | — | The private access token; the same value as the web app's. **Secret.** |
| `REPOATLAS_ANTHROPIC_API_KEY` | required for answers | — | The Anthropic API key. **Secret.** |
| `REPOATLAS_LLM_TRANSPORT` | optional | — | `anthropic` (default). `offline` answers without a model, for checks only, and is refused when a key is set. |
| `REPOATLAS_GITHUB_TOKEN` | recommended | recommended | A GitHub token without scopes: raises the API rate limit for repository checks. **Secret.** |
| `REPOATLAS_TRUST_PROXY_HEADERS` | `true` | — | The visitor's address comes from the web app (`X-Forwarded-For`). Set it only when the API is reachable through the web app alone. |
| `REPOATLAS_RATE_LIMIT_SALT` | recommended | — | Random; keeps hashed visitor keys stable across restarts. **Secret.** |
| `REPOATLAS_WEB_ORIGINS` | optional | — | The web app's origin, for CORS. Not needed when every call comes through the web app. |
| `REPOATLAS_LOG_FORMAT` | `json` | `json` | Set by the images. |
| `REPOATLAS_DATA_DIR` | `/data` | `/data` | Set by the images. |
| `REPOATLAS_MODEL_CACHE_DIR`, `REPOATLAS_EMBEDDING_MODEL`, `HF_HUB_OFFLINE` | set by the image | set by the image | The model is inside the image; don't change these. |
| Limits | see below | see below | `infra/production.env.example` |

Limits for a deployment others can reach are in
[`infra/production.env.example`](../infra/production.env.example) (daily spend cap, per-visitor
caps, concurrent builds, repository size caps). Copy its lines into the backend's variables.
`REPOATLAS_DAILY_SPEND_CAP_USD`, the per-visitor caps, and `REPOATLAS_ANSWER_MAX_COST_USD`
matter to the API; `REPOATLAS_JOB_MAX_RUNNING`, `REPOATLAS_JOB_TIMEOUT_S`, and the repository
size caps matter to both.

Start commands (the images' defaults):

- API: `uvicorn repoatlas.api.app:create_app --factory --host 0.0.0.0 --port 8000`; health
  check path `/api/v1/health` (open); readiness `/api/v1/ready` (needs the token).
- Worker: `repoatlas worker`; stops gracefully on SIGTERM (the running build returns to the
  queue).
- Migrations: `repoatlas db upgrade`, once per release, before the new version starts (Railway:
  the API service's pre-deploy command; Fly: `release_command`). The release workflow also runs
  it as a step.

### Web app (Vercel)

| Variable | When | Value |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | build | `/` — the browser calls the web app's own origin. |
| `REPOATLAS_API_ORIGIN` | run time | The API's address, for example `https://repoatlas-api.up.railway.app`. Server-side only. |
| `REPOATLAS_ACCESS_TOKEN` | run time | The private access token; the same value as the API's. **Secret**, server-side only (no `NEXT_PUBLIC_` prefix). |

Project settings: root directory `frontend`, framework Next.js, Node 22. Leave `NEXT_OUTPUT`
unset (standalone output is for the Docker image). The forwarding route streams server-sent
events; a question can take up to two minutes, so set the function's maximum duration to at
least 120 seconds.

### Release workflow (GitHub)

`.github/workflows/release.yml` publishes and deploys only when the repository **variable**
`DEPLOY_ENABLED` is `true`. It then needs:

| Kind | Name | Value |
|---|---|---|
| Variable | `DEPLOY_ENABLED` | `true` to switch publishing and deploying on |
| Variable | `DEPLOY_PROVIDER` | `railway` or `fly` |
| Variable | `PUBLIC_URL` | The web app's address, for the smoke check |
| Variable | `RAILWAY_API_SERVICE`, `RAILWAY_WORKER_SERVICE` | Railway service names (Railway only) |
| Variable | `FLY_API_APP`, `FLY_WORKER_APP` | Fly app names (Fly only) |
| Secret | `DATABASE_URL` | For the migration step |
| Secret | `REPOATLAS_ACCESS_TOKEN` | For the smoke check |
| Secret | `RAILWAY_TOKEN` or `FLY_API_TOKEN` | Deploy token of the chosen provider |
| Secret | `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | For the web deploy |

Put the secrets in a GitHub environment named `production` (the deploy job uses it), so a
required reviewer can approve each deploy.

## Choosing the backend provider

The comparison is in decision
[0020](decisions/0020-build-without-spend-live-pass.md#provider-comparison-on-paper). Both fit
the images and the workflow. Before provisioning, confirm on the provider's site: `vector` and
`pg_trgm` on the Postgres plan, 2 GB of memory for the worker, and how a service is stopped
between sessions.

## First deployment (the live pass)

1. Create the Postgres database; run `CREATE EXTENSION IF NOT EXISTS vector;` and
   `CREATE EXTENSION IF NOT EXISTS pg_trgm;` once if the provider doesn't allow the migration
   role to create extensions (the first migration creates them otherwise).
2. Create the API and worker services from the images, with the variables above.
3. Create the Vercel project with the variables above.
4. Set the GitHub variables and secrets; set `DEPLOY_ENABLED` to `true`.
5. Tag a release (`v0.1.0`) and push the tag. The workflow builds, checks, publishes,
   migrates, deploys, and smoke-checks.
6. Run the end-to-end check against it:
   `python scripts/selfhost_check.py --base-url https://<web-host> --token "$TOKEN" --expect-live`.
7. Roll back once to prove it: run the workflow by hand with the previous version.
8. Record the results in [live-pass.md](live-pass.md) and in the milestone's Results.

Pausing, resuming, and tearing down are in the [runbook](runbook.md).
