# M12 — Private deployment & limits

**Type of work:** DevOps · **Size:** M (8 tasks) · **Status:** Done

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
- **Delivered details** (backward compatible with the above):
  - `SpendGuard(engine, cap_usd, *, ttl_s=30, clock, now)` with `spent_today()`, `check()` (raises `repoatlas.llm.types.SpendCapReached`), `record(cost_usd)`, and `from_settings(settings, engine)` (`None` without a cap). `LLMClient(…, guard=…)` asks it before every request, repair calls included; `OneShotAnswerSystem` and `ImpactExplainer` turn `SpendCapReached` into a `budget` refusal. Settings: `daily_spend_cap_usd` (5; unset for no cap), `builds_per_day` (3), `asks_per_day` (20), `job_max_running` (2), `access_token`, `trust_proxy_headers`, `llm_transport`, `model_cache_dir`. Migration `0008_spans_llm_started_at` (a partial index for the daily total).
  - `repoatlas.api.access`: `AccessGateMiddleware(app, *, token, prefix)`, `cookie_value(token)` (sha256 of `repoatlas-access:` and the token; the web app computes the same), cookie name `repoatlas_access`. Open without the token: `/api/v1/health` and CORS preflights. Error code `unauthorized` (401).
  - Web app: `src/proxy.ts` (the gate), `/access` and `POST /access/enter` (token entry; HTTP-only cookie for 30 days, `Secure` on HTTPS), `/api/v1/[...path]` (forwards to `REPOATLAS_API_ORIGIN` with the token and the visitor's address; streams), `/healthz`. Build with `NEXT_PUBLIC_API_URL=/` to use the forwarding route; `NEXT_OUTPUT=standalone` for the image.
  - `/ready` answers ready with `database: "ahead"` on a schema newer than the code, and `repoatlas db upgrade` then does nothing, so the previous release starts after a rollback.
  - `repoatlas models fetch` (downloads the embedding model; the image builds run it). `scripts/selfhost_check.py` (`--env-file` or `--base-url` and `--token`; `--only gate`, `--skip-build ID`, `--expect-offline`, `--expect-live`, `--json FILE`): the end-to-end check of a running deployment.
  - The ask stream sends a `: keep-alive` comment after every 15 seconds of silence.
  - `infra/selfhost.env.example` (settings of the self-host stack), `docs/m12-gate-evidence.md` (what was run for the gate).

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

- [x] **T1 — Production images.** API, worker, and web Dockerfiles; local check that they build and start.
- [x] **T2 — Self-host compose and guide.** A fresh database migrates; the stack comes up healthy.
- [x] **T3 — Provider comparison and deployment reference.** Decision 0020, `docs/deploy.md` (every environment variable per service), `docs/live-pass.md`. Provisioning is a live-pass item.
- [x] **T4 — Release workflow.** Image builds and a compose smoke check in CI; push, migration, deploy, and readiness jobs behind `DEPLOY_ENABLED`.
- [x] **T5 — Frontend ready for Vercel.** Environment wiring (API origin, same-origin proxy) and the web side of the access gate; tests. Deploying is a live-pass item.
- [x] **T6 — Spend guard and caps.** Implementation and tests (cap reached → budget refusal; per-client caps → 429; concurrency cap respected).
- [x] **T7 — Private access gate.** API middleware; tests (missing or wrong token rejected).
- [x] **T8 — End-to-end check and runbook.** On the local self-host stack: build a repo, ask a question (offline stand-in), run impact analysis, roll back to earlier images; write the runbook.

## Exit criteria

- [x] On the local self-host stack: a repo builds, a question returns a cited answer through the offline stand-in, and impact analysis returns a report.
- [x] Spend-cap, per-client cap, and access-gate tests pass.
- [x] Starting the stack runs migrations automatically, and a rollback to earlier images works on the migrated database.
- [x] The runbook covers deploy, rollback, pause, resume, teardown, and restore.

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

Gate checked on 2026-11-14 on branch `m12-deploy-limits`, on Windows 11 with Docker 29.9.2 and Postgres up. **This is the build gate of decision 0020: it was reached without spending anything. The hosted deployment and the live answer check are not done; they are pending in [`docs/live-pass.md`](../live-pass.md).**

**Exit criteria.** What was run, with the output, is in [`docs/m12-gate-evidence.md`](../m12-gate-evidence.md).

- **Local self-host stack.** On a fresh `infra/compose.selfhost.yml` stack (production images, the real embedding model inside them, `REPOATLAS_LLM_TRANSPORT=offline`), `scripts/selfhost_check.py` passed every step: the gate rejects a missing or wrong token; the token page sets the cookie; `/ready` is at `0008_spans_llm_started_at`; `pallets/itsdangerous` (commit `672971d`) builds to a ready snapshot; a question streams `retrieving`, `answering`, `validating` and returns 3 claims with 3 valid citations into `src/itsdangerous/signer.py`; impact analysis on `want_bytes` returns 56 symbols (30 callers, 24 tests, 2 public APIs). The answer is the offline stand-in's and says so; no model was called.
- **Limit and gate tests.** `tests/integration/limits` (12 tests: the daily total from `spans`, the 30-second cache, the alert, a new day, an ask over the cap refusing with `budget`, the job cap with racing claims), `tests/unit/api/test_access.py` (22: missing and wrong tokens in every form, header and cookie, open routes, no token in logs), `tests/unit/api/test_ratelimit.py` (daily caps → 429 until midnight UTC, trusted proxy addresses), and the web app's 45 gate and forwarding tests pass.
- **Migrations and rollback.** Starting the stack ran `0007 → 0008` before the API and the worker started. With a stand-in next release (migration 0009) on the database, rolling back to this release with the runbook's `up -d` worked: its migration step did nothing, `/ready` said `ahead`, and a question and an impact request passed. Rolling back further to images of commit `d631d47` (from before migration 0008) passed the same check.
- **Runbook.** `docs/runbook.md` covers deploy, rollback, pause, resume, backup, restore, and teardown. Each self-host procedure was run on the stack (after teardown and restore, the earlier snapshot answered again without a rebuild). The hosted procedures are written and marked not yet run.

**Tests.** In `backend/`, `uv run poe check` passes: 1,522 tests passed, 1 skipped (symlinks need extra privileges on Windows), 7 deselected (the `live` tests); ruff, mypy (384 files), and the docs lint pass. Frontend: lint, typecheck, and 299 unit tests pass; Playwright passes 12 of 12 (stress test: median expansion 753 ms, slowest 1,212 ms). The OpenAPI spec and TypeScript types are current.

**Live pass (pending).** Five items in `docs/live-pass.md`: the backend on Railway or Fly with managed Postgres, the frontend on Vercel, a release and a rollback through the workflow, the end-to-end check on the hosted deployment with a live answer, and a live answer on the self-host stack. DEP-3 (hosted deployment) is built for but not delivered. The release workflow's publish and deploy jobs are written and have never run; its provider commands are unconfirmed.

**Found by running the stack** (not by unit tests): the token-page redirect returned 500 in the built web image (Next.js rejects a relative `Location` from `proxy.ts`); a rolled-back release could not start, because `/ready` and `repoatlas db upgrade` both treated a newer schema as an error; the spend-cap alert raised when its log stream was closed, turning a refusal into an error.

**Deviations** (decision 0020 covers the first three).
- No hosted deployment, no provider chosen, and no live LLM call. T3 compares the providers on paper, T5 makes the web app ready for Vercel without deploying it, and T8's check runs on the local stack through the offline stand-in.
- The web app serves the API under its own origin instead of calling it across origins, so the HTTP-only cookie covers API calls and the API needs no public address. A third image (`web.Dockerfile`) makes the self-host stack complete.
- Earlier contracts gained backward-compatible additions (`/ready` `ahead`, `unauthorized`, daily caps, the LLM client's guard); decision 0020 lists them.
- Per-client daily caps are counted in the API's memory, like the hourly buckets: they start again when the API restarts and need one API instance. The spend cap is read from the database and has neither limit.
- Published without a reverse proxy, the self-host web app can't tell visitors apart reliably (a caller can set the forwarding headers). The guide says so; the token and the spend cap don't depend on addresses.
- The gate has no Playwright spec (it would need a third web build); the stack check covers it over HTTP, in CI too.
- The stack run used images built at commit `ebdd171`; the later commits change the spend guard's alert handling, tests, and docs.

**Cost.** $0: no LLM call, no hosting, no new accounts.
