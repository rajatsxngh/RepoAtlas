# M12 — Private deployment & limits

**Type of work:** DevOps · **Size:** M (8 tasks) · **Status:** Not started

## Goal

Run the whole product in the cloud — privately — with hard limits on cost and abuse, so infrastructure problems surface long before launch.

## Depends on

- **M00–M11 (all earlier milestones)** — this milestone deploys what they built. Specifically: settings and migrations (M00); fetching and caps (M01); build pipeline and store (M02, M03); API, worker, stage registry, rate limits (M04); web app (M05); retrieval with the local embedding model (M06); LLM client, tracing, ask API (M07); impact API and UI (M11). The sandbox, benchmark, and eval tooling (M08–M10) stay offline and are not deployed.

## Provides

- **Images:** `infra/docker/api.Dockerfile` and `infra/docker/worker.Dockerfile` — multi-stage, non-root, pinned base image digests; the worker image includes the embedding model (downloaded at build time) so cold starts don't download it.
- **Self-host:** `infra/compose.selfhost.yml` (Postgres + pgvector, API, worker, web) and `docs/self-hosting.md`.
- **Hosted deployment:** backend (API and worker services) on Railway or Fly — the choice is recorded in a decision; managed Postgres with the `vector` and `pg_trgm` extensions; frontend on Vercel. `docs/deploy.md` lists every environment variable per service.
- **Release workflow:** `.github/workflows/release.yml` — build and push images, run `repoatlas db upgrade` as a release step, deploy, smoke-check `/ready`.
- **Limits:**
  - `repoatlas.limits.SpendGuard` — a daily USD cap across all LLM calls, checked before each call (from traced spend); over the cap, answers return a `budget` refusal.
  - Per-client daily question caps and build caps; job concurrency cap in the worker; production size caps in settings.
- **Private access gate:** `REPOATLAS_ACCESS_TOKEN` required by the API (header or cookie) and checked by a Next.js middleware on the web app; unlisted URLs.
- **Runbook:** `docs/runbook.md` — deploy, roll back, pause (scale to zero), resume, tear down, restore the database from backup.

## Requirements

DEP-1, DEP-2, DEP-3, DEP-4, DEP-5, DEP-6, DEP-7

## Scope

**In:** production images, self-host compose, hosted deployment, release workflow with migrations, spend and usage caps, private access gate, runbook, an end-to-end check on the deployment.

**Out:** public launch, precomputed repos, deploying the sandbox or evaluation tooling.

## Design notes

- **Provider choice:** compare Railway and Fly on managed Postgres with `pgvector` support, worker memory for CPU embeddings (≥2 GB), long-lived connections for server-sent events, and the ability to scale to zero. Record the decision.
- **Server-sent events through proxies:** keep-alive comments every 15 seconds; generous idle timeouts; disable response buffering.
- **Migrations:** run once per release, before new API instances start; migrations are backward compatible with the previous release so rollbacks work.
- **Spend guard:** a daily total is computed from the `spans` table (cost of LLM spans since midnight UTC) and cached for 30 seconds; the guard runs before every LLM call; when tripped, an alert is logged and answers refuse with `budget`.
- **Caps (defaults, configurable):** 20 questions per client per day, 3 builds per client per day, 2 concurrent build jobs, production repo caps no larger than development caps.
- **Access gate:** constant-time token comparison; the token never appears in logs; the web app sets an HTTP-only cookie after a one-time token entry.
- **Cost of hosting:** the deployment can be paused between work sessions; the runbook explains how.
- **Secrets:** stored in the provider's secret manager; `.env` files are never deployed.

## Tasks

- [ ] **T1 — Production images.** API and worker Dockerfiles; local check with compose that a fresh database migrates and a build, an ask, and an impact request work.
- [ ] **T2 — Self-host compose and guide.**
- [ ] **T3 — Provider decision and managed Postgres.** Compare, decide, provision, confirm extensions.
- [ ] **T4 — Release workflow.** Image build and push, migrations, deploy, readiness smoke check.
- [ ] **T5 — Frontend on Vercel.** Environment wiring (API base URL, access gate).
- [ ] **T6 — Spend guard and caps.** Implementation and tests (cap reached → budget refusal; per-client caps → 429; concurrency cap respected).
- [ ] **T7 — Private access gate.** API dependency and web middleware; tests (missing or wrong token rejected).
- [ ] **T8 — End-to-end check and runbook.** On the deployment: build a repo, ask a question, run impact analysis; write the runbook; pause the deployment afterwards if desired.

## Exit criteria

- [ ] On the deployment: a repo builds, a question returns a cited answer, and impact analysis returns a report.
- [ ] Spend-cap, per-client cap, and access-gate tests pass.
- [ ] A release runs migrations automatically and a rollback to the previous release works.
- [ ] The runbook covers deploy, rollback, pause, resume, teardown, and restore.

## Verification

```bash
docker compose -f infra/compose.selfhost.yml up -d --build
cd backend && uv run pytest tests/integration/limits -q
curl -H "authorization: Bearer $REPOATLAS_ACCESS_TOKEN" https://<api-host>/api/v1/ready
```

Manual: the end-to-end check in the browser on the deployed web app.

## Risks and open questions

- Managed Postgres plans differ in extension support; confirm `vector` before committing to a provider.
- CPU embedding on small instances is slow; precomputing and caching mitigate it.
- Open: whether to use the provider's built-in Postgres backups or scheduled dumps.

## Results

_Filled in at the gate._
