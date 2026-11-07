# M04 — API & background jobs

**Type of work:** Backend service · **Size:** M (8 tasks) · **Status:** In progress

## Goal

Expose snapshots over a typed HTTP API and run builds as resumable background jobs with visible progress, with pipeline stages that new work can plug into without touching the job runner.

## Depends on

- **M01** — `parse_repo_url`, `GitHubClient` (public check, size, head SHA), `RepoTooLarge` and related errors.
- **M03** — schema and models, `save_snapshot`, `GraphStore` (all read methods), `SnapshotGraphCache`, the build step functions, `fingerprint.compute`.
- **M00** — settings, logging, CLI, test fixtures, CI.

## Provides

- **App:** `repoatlas.api.app:create_app(settings=None) -> FastAPI` (uvicorn `--factory`); routers under `/api/v1`; error model `{"error": {"code", "message", "details"}}` (`repoatlas.api.errors.ErrorResponse`) with the stable codes of `ErrorCode`: `invalid_request`, `invalid_url`, `repo_private`, `repo_too_large`, `repo_empty`, `not_found` (with `details.resource`), `rate_limited`, `snapshot_not_ready`, `upstream_unavailable`, `internal` (with `details.request_id`); every response carries `X-Request-ID`; CORS for the comma-separated `web_origins` setting.
- **Endpoints** (shapes in PRD §8): `POST /repos`, `GET /jobs/{job_id}`, `GET /snapshots/{id}`, `GET /snapshots/{id}/subgraph`, `GET /snapshots/{id}/nodes/{key}`, `GET /snapshots/{id}/locate`, `GET /snapshots/{id}/nodes/{key}/neighbors`, `GET /snapshots/{id}/search`, `GET /snapshots/{id}/path`, `GET /snapshots/{id}/code`, `GET /health`, `GET /ready`. `GET /snapshots/{id}` answers in any status (with its latest build `job`); every other snapshot read needs a ready snapshot (409 `snapshot_not_ready` with `snapshot_id`, `status`, `job_id`, `job_status`, `error`). Reads return the store's models (`Node`, `Neighborhood`, `GraphPath`, `Subgraph`, `CodeSlice`, `SymbolMatch`); `nodes/{key}` returns `{node, owners, degree: {incoming, outgoing}}` and `search` returns `{query, results}`. Kinds are repeated query parameters (`kinds=calls&kinds=imports`); `path` takes `from` and `to`; `code` takes `path`, `start` (default 1), and `end` (default: as many lines as allowed). File paths must be repo-relative (no empty, `.`, or `..` segments: 422), strings may not hold NUL characters, and IDs and line numbers are bounded to their Postgres types. A symbol key is one URL-encoded path segment (`EncodedKeyMiddleware` keeps it whole) and is decoded exactly once.
- **Jobs:** migration `0003_jobs` (PRD §7.1 columns plus `snapshot_id`, `dedupe_key` (one queued or running job per key), `worker_id`, `run_after`, `started_at`, `finished_at`); `repoatlas.jobs.queue` — `Job`, `Lease` (job, worker, attempt: fences every write; `LeaseLost` otherwise), `enqueue(kind, payload, *, snapshot_id, dedupe_key, max_attempts, delay_s, connection)`, `claim(worker_id, *, kinds, lease_s)` (`FOR UPDATE SKIP LOCKED`), `heartbeat`, `report(lease, stage, progress)`, `complete`, `fail(lease, error, *, retry)` (retries after 10 s, 60 s, 300 s until `max_attempts`), `release` (gives the attempt back), `recover_expired` (an expired lease counts as a failed attempt), `get_job`, `latest_job(snapshot_id)`. `repoatlas.jobs.worker` — `Worker(settings, …)` with `run()`, `run_once()`, `request_stop()`, a stop file, heartbeats, and a per-attempt timeout; `JobHandler(kind, run(ctx), on_failed, permanent_errors)`, `register_job_handler`, `JobContext` (`report`, `check`, `JobInterrupted`). `repoatlas.jobs.build` — the `build` job (`BuildPayload`, `enqueue_build`, `dedupe_key`), which refuses to run with another pipeline's fingerprint. `repoatlas worker [--once] [--stop-file PATH] [--id NAME]` stops gracefully on Ctrl+C, Ctrl+Break, SIGTERM, or the stop file; a second Ctrl+C stops at once. Settings `job_lease_s`, `job_heartbeat_s`, `job_max_attempts`, `job_retry_backoff_s`, `job_timeout_s`, `worker_poll_s`, `db_connect_timeout_s`.
- **Stage registry:** `repoatlas.pipeline.stages` — `Stage(name, version, settings_keys, run(ctx) -> None, weight, components)`, `register_stage(stage, after=None)`, `unregister_stage(name)`, `registered_stages()`, `fingerprint_parts(settings, stages=None)`, `config_fingerprint(settings, stages=None)`, and `run_stages(ctx, stages=None, *, report, check)`. Stages run over `repoatlas.pipeline.context.BuildContext`, which adds `workdir` (checkpoints between attempts), `resumed`, and `progress(fraction)`. The build job and `repoatlas build` run the registered stages in order, reporting `stage` and `progress`. The initial stages are `fetch`, `inventory`, `parse`, `graph`, `store`; each keeps its output in `pipeline.build.build_dir(settings, repo, commit, fingerprint)` and skips the work when it is already there. `repoatlas.pipeline.parse.parse_files` takes an optional `progress(done, total)`.
- **Snapshot reuse:** a build request for a (repo, commit, fingerprint) that already exists returns the ready snapshot, or the in-flight job; a new fingerprint means a new snapshot. `repoatlas.jobs.build.request_build(url, settings, *, engine) -> BuildRequest(snapshot_id, job_id, reused, status)` does it all: GitHub checks outside any transaction, then, with the snapshot row locked (`repoatlas.store.loader.request_snapshot`), reuse or `mark_pending` and enqueue. A failed snapshot, or one whose build was lost, gets a new build; concurrent requests for one snapshot queue one build. `POST /repos` takes `{url}` and answers 202 `{snapshot_id, job_id, reused, status}` (`job_id` is null for a ready snapshot built outside the queue); `GET /jobs/{job_id}` answers status, stage, progress, attempts, the last error (internal errors show only their type), and timestamps.
- **Rate limiting:** middleware with per-client token buckets (client key = sha256 of IP + salt), separate limits for `build` and `read` route groups; responses 429 with `Retry-After`.
- **OpenAPI:** `repoatlas api openapi --out backend/openapi.json`; `scripts/gen_api_types.(ps1|sh)` running `npx openapi-typescript backend/openapi.json -o frontend/src/lib/api/schema.d.ts`; the generated file is committed.

## Requirements

API-1, API-2, API-3, API-4, API-5, API-6, API-7, API-8

## Scope

**In:** HTTP API for builds and graph reads, job queue and worker, stage registry and fingerprint, validation, rate limits, OpenAPI and type generation, API tests.

**Out:** web UI, retrieval, LLM calls, deployment.

## Design notes

- **Build request flow:** validate the URL (`parse_repo_url`) → GitHub metadata (private → 403 `repo_private`; size over cap → 413 `repo_too_large`) → resolve head SHA → compute fingerprint → look up an existing snapshot or in-flight job → otherwise create the snapshot (`pending`) and enqueue a `build` job → `202 { snapshot_id, job_id, reused }`.
- **Job lifecycle:** `queued → running → succeeded | failed`. Leases last 60 seconds and heartbeats run every 15 seconds. A job whose lease expires goes back to `queued` with `attempts + 1`, up to `max_attempts = 3`, with backoff 10 s, 60 s, 300 s.
- **Idempotent stages:** each stage checks whether its output already exists for the snapshot and skips if so, so a retried job resumes instead of redoing work. The store stage keeps its single-transaction guarantee.
- **Progress:** `progress = (sum of weights of finished stages + current stage fraction × weight) / total weight`; stages may report their own fraction (for example files parsed).
- **Fingerprint:** sha256 over the ordered list of (stage name, stage version, values of its `settings_keys`). Adding or changing a stage changes the fingerprint, so old snapshots are never silently mixed with new pipeline output.
- **Reads before ready:** endpoints on a snapshot that isn't `ready` return 409 `snapshot_not_ready` with the job ID.
- **Keys in paths:** symbol keys are URL-encoded in path segments; the router decodes them once.
- **Code reads:** at most 400 lines per request; the path must belong to the snapshot (`GraphStore.read_code` already enforces this).
- **Search:** `pg_trgm` similarity on `name` and `qualified_name`, with exact and prefix matches ranked first; `kinds` filter; limit capped at 50.
- **Rate limits:** in-process buckets (one API instance in v1). Defaults: 5 builds per hour and 120 reads per minute per client; configurable.
- **Testing:** `httpx.AsyncClient` against `create_app` with a real Postgres (integration marker); the worker is tested by running its loop in a thread with a short lease.

## Tasks

- [x] **T1 — App skeleton.** `create_app`, settings, error model and exception handlers, CORS, `/health` and `/ready` (ready checks the database).
- [x] **T2 — Job queue and worker.** Migration, queue functions, worker loop, graceful shutdown; tests for claim exclusivity (two workers), lease expiry requeue, retries and backoff.
- [x] **T3 — Stage registry and fingerprint.** Registry API; the build job runs the existing steps as stages; progress reporting; test that registering a dummy stage changes the fingerprint.
- [x] **T4 — `POST /repos`.** Validation, GitHub checks (recorded responses), reuse, enqueue.
- [x] **T5 — Read endpoints.** Snapshot, subgraph, node, locate, neighbors, search, path, code — thin wrappers over `GraphStore` with response models.
- [ ] **T6 — Rate limiting.** Middleware and tests (429 with `Retry-After`).
- [ ] **T7 — OpenAPI and TypeScript types.** Export command, generation script, committed `schema.d.ts`, CI check that the committed spec is up to date.
- [ ] **T8 — API test suite.** Happy paths plus abuse cases: invalid, private, and oversize URLs; path traversal in `/code` (`../`, absolute paths, encoded variants); unknown snapshot; not-ready snapshot.

## Exit criteria

- [ ] API tests pass, including invalid/private/oversize URLs, 429 responses, and path traversal attempts.
- [ ] Killing a worker mid-build: after the lease expires, another worker resumes and the snapshot becomes `ready`.
- [ ] Registering a dummy stage changes the fingerprint, and a new build request creates a new snapshot.
- [ ] `backend/openapi.json` and `frontend/src/lib/api/schema.d.ts` are generated and committed; CI fails if they are stale.

## Verification

```bash
cd backend
uv run pytest tests/integration/api tests/integration/jobs -q
uv run uvicorn repoatlas.api.app:create_app --factory --reload
uv run repoatlas worker
curl -X POST localhost:8000/api/v1/repos -H "content-type: application/json" -d "{\"url\":\"https://github.com/<owner>/<repo>\"}"
curl localhost:8000/api/v1/jobs/<job_id>
```

## Risks and open questions

- Signal handling differs on Windows (no SIGTERM from the console); the worker also stops on Ctrl+C and on a stop file.
- In-process rate limits don't work across several API instances; acceptable for v1 and noted in the decision record.
- Open: whether long builds need a dedicated "cancel job" endpoint (not required for the gate).

## Results

_Filled in at the gate._
