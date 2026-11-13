# M12 — Private deployment & limits

**Type of work:** DevOps · **Size:** M (8 tasks) · **Status:** In progress

## Goal

Run the whole product in the cloud — privately — with hard limits on cost and abuse, so infrastructure problems surface long before launch.

**Replanned on 2026-11-14 (decision 0020):** nothing is spent while the project is being built. This milestone builds and checks everything on a local self-host stack; the hosted deployment and the live answer check are **live-pass items**, pending until the owner approves the spending (see "Live pass" under Exit criteria and [`docs/live-pass.md`](../live-pass.md)).

## Depends on

- **M00–M11 (all earlier milestones)** — this milestone deploys what they built. Specifically: settings and migrations (M00); fetching and caps (M01); build pipeline and store (M02, M03); API, worker, stage registry, rate limits (M04); web app (M05); retrieval with the local embedding model (M06); LLM client, tracing, ask API (M07); impact API and UI (M11). The sandbox, benchmark, and eval tooling (M08–M10) stay offline and are not deployed.

## Provides

- **Images:** `infra/docker/api.Dockerfile`, `infra/docker/worker.Dockerfile`, and `infra/docker/web.Dockerfile` — multi-stage, non-root, pinned base image digests; the worker image includes the embedding model (downloaded at build time) so cold starts don't download it.
- **Self-host:** `infra/compose.selfhost.yml` (Postgres + pgvector, a one-shot `migrate` service, API, worker, web) and `docs/self-hosting.md`.
- **Hosted deployment (built for, not delivered; live pass):** `docs/deploy.md` lists every environment variable per service for a backend on Railway or Fly, managed Postgres with the `vector` and `pg_trgm` extensions, and the frontend on Vercel. The provider comparison is in decision 0020; the owner picks at the live pass.
- **Release workflow:** `.github/workflows/release.yml` — builds the three images and smoke-checks the compose stack (migrations, `/ready`, the gate); jobs that push images, run `repoatlas db upgrade` as a release step, deploy, and smoke-check `/ready` are written and run only when the `DEPLOY_ENABLED` repository variable is `true`.
- **Limits:**
  - `repoatlas.limits.SpendGuard` — a daily USD cap across all LLM calls, checked before each call (from traced spend); over the cap, answers return a `budget` refusal.
  - Per-client daily question caps and build caps; job concurrency cap in the worker; production size caps in settings (`infra/production.env.example`).
- **Private access gate:** `REPOATLAS_ACCESS_TOKEN` required by the API (header or cookie) and checked by the web app's request proxy (`src/proxy.ts`, the Next.js 16 name for middleware); unlisted URLs. The web app serves the API under its own origin (`/api/v1/*` forwarded to `REPOATLAS_API_ORIGIN`), so the browser's HTTP-only cookie covers API calls.
- **Offline stand-in:** `REPOATLAS_LLM_TRANSPORT=offline` (decision 0020) — a transport that calls no model, for stack checks without a key.
- **Runbook:** `docs/runbook.md` — deploy, roll back, pause (scale to zero), resume, tear down, restore the database from backup.
- **Live-pass list:** `docs/live-pass.md` — the checks that need spending, each with the steps to run it.

## Requirements

DEP-1, DEP-2, DEP-3, DEP-4, DEP-5, DEP-6, DEP-7

## Scope

**In:** production images, self-host compose, everything a hosted deployment needs short of provisioning it, release workflow with migrations, spend and usage caps, private access gate, runbook, an end-to-end check on the local self-host stack.

**Out:** public launch, precomputed repos, deploying the sandbox or evaluation tooling; provisioning, hosting, and live LLM calls (live pass, decision 0020).

## Design notes

- **Provider choice:** compare Railway and Fly on managed Postgres with `pgvector` support, worker memory for CPU embeddings (≥2 GB), long-lived connections for server-sent events, and the ability to scale to zero. The comparison is in decision 0020; the choice is made at the live pass.
- **Server-sent events through proxies:** keep-alive comments every 15 seconds; generous idle timeouts; disable response buffering.
- **Migrations:** run once per release, before new API instances start; migrations are backward compatible with the previous release so rollbacks work.
- **Spend guard:** a daily total is computed from the `spans` table (cost of LLM spans since midnight UTC) and cached for 30 seconds; the guard runs before every LLM call; when tripped, an alert is logged and answers refuse with `budget`.
- **Caps (defaults, configurable):** 20 questions per client per day, 3 builds per client per day, 2 concurrent build jobs, production repo caps no larger than development caps.
- **Access gate:** constant-time token comparison; the token never appears in logs; the web app sets an HTTP-only cookie after a one-time token entry.
- **Cost of hosting:** the deployment can be paused between work sessions; the runbook explains how.
- **Secrets:** stored in the provider's secret manager; `.env` files are never deployed.

## Tasks

- [ ] **T1 — Production images.** API, worker, and web Dockerfiles; local check that they build and start.
- [ ] **T2 — Self-host compose and guide.** A fresh database migrates; the stack comes up healthy.
- [ ] **T3 — Provider comparison and deployment reference.** Decision 0020, `docs/deploy.md` (every environment variable per service), `docs/live-pass.md`. Provisioning is a live-pass item.
- [ ] **T4 — Release workflow.** Image builds and a compose smoke check in CI; push, migration, deploy, and readiness jobs behind `DEPLOY_ENABLED`.
- [ ] **T5 — Frontend ready for Vercel.** Environment wiring (API origin, same-origin proxy) and the web side of the access gate; tests. Deploying is a live-pass item.
- [ ] **T6 — Spend guard and caps.** Implementation and tests (cap reached → budget refusal; per-client caps → 429; concurrency cap respected).
- [ ] **T7 — Private access gate.** API middleware; tests (missing or wrong token rejected).
- [ ] **T8 — End-to-end check and runbook.** On the local self-host stack: build a repo, ask a question (offline stand-in), run impact analysis, roll back to earlier images; write the runbook.

## Exit criteria

- [ ] On the local self-host stack: a repo builds, a question returns a cited answer through the offline stand-in, and impact analysis returns a report.
- [ ] Spend-cap, per-client cap, and access-gate tests pass.
- [ ] Starting the stack runs migrations automatically, and a rollback to earlier images works on the migrated database.
- [ ] The runbook covers deploy, rollback, pause, resume, teardown, and restore.

### Live pass (pending; not part of this gate)

These need spending the owner has deferred (decision 0020). They are not checked here and must never be reported as passed until they are run:

- On a hosted deployment: a repo builds, a question returns a cited answer from the live model, and impact analysis returns a report.
- The backend runs on Railway or Fly with managed Postgres (`vector`, `pg_trgm` confirmed); the frontend runs on Vercel.
- A release through the workflow runs migrations automatically, and a rollback to the previous release works on the deployment.

## Verification

```bash
docker compose -f infra/compose.selfhost.yml --env-file infra/selfhost.env up -d --build
cd backend && uv run pytest tests/integration/limits -q
curl -H "authorization: Bearer $REPOATLAS_ACCESS_TOKEN" http://localhost:8080/api/v1/ready
python scripts/selfhost_check.py --base-url http://localhost:8080 --token "$REPOATLAS_ACCESS_TOKEN"
```

Live pass: the same check against the hosted URL, and in the browser on the deployed web app.

## Risks and open questions

- Managed Postgres plans differ in extension support; confirm `vector` before committing to a provider.
- CPU embedding on small instances is slow; precomputing and caching mitigate it.
- Open: whether to use the provider's built-in Postgres backups or scheduled dumps.

## Results

_Filled in at the gate._
