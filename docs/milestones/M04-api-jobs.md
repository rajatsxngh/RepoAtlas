# M04 — API & background jobs

**Type of work:** Backend service · **Size:** M (8 tasks) · **Status:** In progress

## Goal

Expose snapshots over a typed HTTP API and run builds as resumable background jobs with visible progress, with pipeline stages that new work can plug into without touching the job runner.

## Depends on

- **M01** — `parse_repo_url`, `GitHubClient` (public check, size, head SHA), `RepoTooLarge` and related errors.
- **M03** — schema and models, `save_snapshot`, `GraphStore` (all read methods), `SnapshotGraphCache`, the build step functions, `fingerprint.compute`.
- **M00** — settings, logging, CLI, test fixtures, CI.

## Provides

- **App:** `repoatlas.api.app:create_app(settings=None) -> FastAPI` (uvicorn `--factory`); routers under `/api/v1`; error model `{"error": {"code", "message", "details"}}` (`repoatlas.api.errors.ErrorResponse`) with the stable codes of `ErrorCode`: `invalid_request`, `invalid_url`, `repo_private`, `repo_too_large`, `repo_empty`, `not_found` (with `details.resource`), `rate_limited`, `snapshot_not_ready`, `upstream_unavailable`, `internal` (with `details.request_id`); every response carries `X-Request-ID`; request bodies over `max_body_bytes` (64 KB) get 413 `invalid_request` before they are read; CORS for the comma-separated `web_origins` setting.
- **Endpoints** (shapes in PRD §8): `POST /repos`, `GET /jobs/{job_id}`, `GET /snapshots/{id}`, `GET /snapshots/{id}/subgraph`, `GET /snapshots/{id}/nodes/{key}`, `GET /snapshots/{id}/locate`, `GET /snapshots/{id}/nodes/{key}/neighbors`, `GET /snapshots/{id}/search`, `GET /snapshots/{id}/path`, `GET /snapshots/{id}/code`, `GET /health`, `GET /ready`. `GET /snapshots/{id}` answers in any status (with its latest build `job`); every other snapshot read needs a ready snapshot (409 `snapshot_not_ready` with `snapshot_id`, `status`, `job_id`, `job_status`, `error`). Reads return the store's models (`Node`, `Neighborhood`, `GraphPath`, `Subgraph`, `CodeSlice`, `SymbolMatch`); `nodes/{key}` returns `{node, owners, degree: {incoming, outgoing}}` and `search` returns `{query, results}`. Kinds are repeated query parameters (`kinds=calls&kinds=imports`); `path` takes `from` and `to`; `code` takes `path`, `start` (default 1), and `end` (default: as many lines as allowed). File paths must be repo-relative (no empty, `.`, or `..` segments: 422), strings may not hold NUL characters, and IDs and line numbers are bounded to their Postgres types. A symbol key is one URL-encoded path segment (`EncodedKeyMiddleware` keeps it whole) and is decoded exactly once.
- **Jobs:** migration `0003_jobs` (PRD §7.1 columns plus `snapshot_id`, `dedupe_key` (one queued or running job per key), `worker_id`, `run_after`, `started_at`, `finished_at`); `repoatlas.jobs.queue` — `Job`, `Lease` (job, worker, attempt: fences every write; `LeaseLost` otherwise), `enqueue(kind, payload, *, snapshot_id, dedupe_key, max_attempts, delay_s, connection)`, `claim(worker_id, *, kinds, lease_s)` (`FOR UPDATE SKIP LOCKED`), `heartbeat`, `report(lease, stage, progress)`, `complete`, `fail(lease, error, *, retry)` (retries after 10 s, 60 s, 300 s until `max_attempts`), `release` (gives the attempt back), `recover_expired` (an expired lease counts as a failed attempt), `get_job`, `latest_job(snapshot_id)`. `repoatlas.jobs.worker` — `Worker(settings, …)` with `run()`, `run_once()`, `request_stop()`, a stop file, heartbeats, and a per-attempt timeout; `JobHandler(kind, run(ctx), on_failed, permanent_errors)`, `register_job_handler`, `JobContext` (`report`, `check`, `JobInterrupted`). `repoatlas.jobs.build` — the `build` job (`BuildPayload`, `enqueue_build`, `dedupe_key`), which refuses to run with another pipeline's fingerprint. `repoatlas worker [--once] [--stop-file PATH] [--id NAME]` stops gracefully on Ctrl+C, Ctrl+Break, SIGTERM, or the stop file; a second Ctrl+C stops at once. Settings `job_lease_s`, `job_heartbeat_s`, `job_max_attempts`, `job_retry_backoff_s`, `job_timeout_s`, `worker_poll_s`, `db_connect_timeout_s`.
- **Stage registry:** `repoatlas.pipeline.stages` — `Stage(name, version, settings_keys, run(ctx) -> None, weight, components)`, `register_stage(stage, after=None)`, `unregister_stage(name)`, `registered_stages()`, `fingerprint_parts(settings, stages=None)`, `config_fingerprint(settings, stages=None)`, and `run_stages(ctx, stages=None, *, report, check)`. Stages run over `repoatlas.pipeline.context.BuildContext`, which adds `workdir` (checkpoints between attempts), `resumed`, and `progress(fraction)`. The build job and `repoatlas build` run the registered stages in order, reporting `stage` and `progress`. The initial stages are `fetch`, `inventory`, `parse`, `graph`, `store`; each keeps its output in `pipeline.build.build_dir(settings, repo, commit, fingerprint)` and skips the work when it is already there. `repoatlas.pipeline.parse.parse_files` takes an optional `progress(done, total)`.
- **Snapshot reuse:** a build request for a (repo, commit, fingerprint) that already exists returns the ready snapshot, or the in-flight job; a new fingerprint means a new snapshot. `repoatlas.jobs.build.request_build(url, settings, *, engine) -> BuildRequest(snapshot_id, job_id, reused, status)` does it all: GitHub checks outside any transaction, then, with the snapshot row locked (`repoatlas.store.loader.request_snapshot`), reuse or `mark_pending` and enqueue. A failed snapshot, or one whose build was lost, gets a new build; concurrent requests for one snapshot queue one build. `POST /repos` takes `{url}` and answers 202 `{snapshot_id, job_id, reused, status}` (`job_id` is null for a ready snapshot built outside the queue); `GET /jobs/{job_id}` answers status, stage, progress, attempts, the last error (internal errors show only their type), and timestamps.
- **Rate limiting:** middleware with per-client token buckets (client key = sha256 of IP + salt), separate limits for `build` and `read` route groups; responses 429 with `Retry-After`. `repoatlas.api.ratelimit` — `RateLimiter(limits, *, salt, clock, max_clients)` (`check(group, client) -> seconds to wait`, `client_key(address)`, `from_settings`), `RateLimitMiddleware` (inside CORS), `Limit`, `TokenBucket`. `POST /repos` is the `build` group; every other route under `/api/v1` is `read`, except `/health`, `/ready`, and the API docs. Settings `rate_limit_enabled`, `builds_per_hour` (5), `reads_per_minute` (120), `rate_limit_salt` (random per process when unset). The 429 body says the group, limit, period, and wait.
- **OpenAPI:** `repoatlas api openapi --out backend/openapi.json [--check]` (`repoatlas.api.openapi.openapi_spec`, `render_spec`: deterministic JSON, operation IDs are the route functions' names); `scripts/gen_api_types.(ps1|sh) [-Check|--check]` running `npx openapi-typescript backend/openapi.json -o frontend/src/lib/api/schema.d.ts` from `frontend/`, where `package.json` and `package-lock.json` pin `openapi-typescript` 7.13.0 (with its peer `typescript` 5.9.3); both generated files are committed. A unit test fails when the spec is stale, and CI regenerates the types (job `api-types`) and fails on any difference.

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
- [x] **T6 — Rate limiting.** Middleware and tests (429 with `Retry-After`).
- [x] **T7 — OpenAPI and TypeScript types.** Export command, generation script, committed `schema.d.ts`, CI check that the committed spec is up to date.
- [x] **T8 — API test suite.** Happy paths plus abuse cases: invalid, private, and oversize URLs; path traversal in `/code` (`../`, absolute paths, encoded variants); unknown snapshot; not-ready snapshot.

## Exit criteria

- [x] API tests pass, including invalid/private/oversize URLs, 429 responses, and path traversal attempts.
- [x] Killing a worker mid-build: after the lease expires, another worker resumes and the snapshot becomes `ready`.
- [x] Registering a dummy stage changes the fingerprint, and a new build request creates a new snapshot.
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

Gate checked on 2026-11-07 on branch `m04-api-jobs`.

**Tests.** `uv run poe check` (with Postgres) passes on Windows: 792 passed, 1 skipped (creating symlinks needs extra privileges on Windows; that test runs on Linux CI). `uv run pytest tests/integration/api tests/integration/jobs -q` runs 122 tests: API 81 (build requests 36, reads 12, abuse 29, health 3, end to end 1) and jobs 41 (queue 14, worker 13, build job 8, schema 6). New unit suites: API 69 (app 9, errors 20, schemas 10, keys 6, rate limits 19, OpenAPI 5), worker parts 6, stages 6, and the fingerprint tests rewritten for the registry (25).

**Exit criteria**
- *API tests pass, including invalid, private, and oversize URLs, 429 responses, and path traversal attempts.* 14 invalid URLs and 7 malformed bodies are refused before GitHub or the database is touched. Private (403), unknown (404), and oversize (413) repositories, GitHub's rate limit (503 with `Retry-After`), and GitHub failures (502) queue nothing. Clients over their read or build limit get 429 with `Retry-After`, in unit tests and in a build flood against Postgres that leaves one job. On `/code` and `/locate`, 14 traversal paths get 422 and 11 others get 404; raw-encoded variants are decoded once, and an absolute path to a real file outside the snapshot never comes back.
- *Killing a worker mid-build: after the lease expires, another worker resumes and the snapshot becomes ready.* `test_killing_a_worker_process_mid_build_lets_another_worker_finish_it` starts a real worker process, kills it once its build reaches the graph stage (`TerminateProcess` on Windows, `SIGKILL` on Linux), and a second worker finishes the job on attempt 2 from the first one's checkpoints without fetching again. A thread-based test does the same with a crash, and the same happened by hand twice (below).
- *Registering a dummy stage changes the fingerprint, and a new build request creates a new snapshot.* `test_registering_a_dummy_stage_changes_the_fingerprint` (unregistering restores it), `test_registering_a_stage_gives_new_builds_a_new_snapshot` (`run_build`), and `test_a_new_commit_or_pipeline_gets_a_new_snapshot` (`POST /repos` gets a new snapshot and job while the stage is registered, and the first snapshot again once it is removed).
- *`backend/openapi.json` and `frontend/src/lib/api/schema.d.ts` are generated and committed; CI fails if they are stale.* A unit test compares the committed spec with the code (both backend CI jobs), the Linux job runs `repoatlas api openapi --check`, and the new `api-types` job regenerates the types with the pinned `openapi-typescript` 7.13.0 and fails on any difference. Both scripts' check modes exit 1 on a stale file and 0 on a current one. The spec is byte-identical across processes and hash seeds.

**By hand** (the Verification commands): uvicorn and two `repoatlas worker` processes against the dev database (migrated to `0003_jobs`) and GitHub, driven by a PowerShell script.
- zod: `POST /repos` queued snapshot 4 (zod @ `19c40d2`, the default branch's head) as job 1. The first worker was killed with `Stop-Process -Force` 10 s later, in the graph stage at progress 0.72; reads answered 409 with the job's ID. The second worker requeued the job once its lease ran out (60 s after the claim: the first worker died before its first heartbeat), claimed it after the 10 s backoff, and finished in 3 s: `resumed` lists fetch, inventory, and parse (their timings 0.13, 0.01, and 0.29 s, against 3.3 s and 3.2 s for fetch and parse in M03's fresh build). The script's own polling, four times a second, ran into the 120-reads-a-minute limit and got a 429, so this run was checked in the database; the worker stopped through its stop file.
- fastapi: the same script, polling once a second, on fastapi/fastapi @ `1ca09c1` (snapshot 5, job 2: 9,967 nodes, 1,404 of them routes). Killed in the graph stage 11 s after the request; requeued 52 s later with "The worker stopped responding during attempt 1 (its lease ran out)."; resumed after the backoff and ready 5 s after the claim, with `resumed` fetch, inventory, and parse. The root subgraph had 5 children and 17 aggregated edges, a second `POST /repos` answered `reused: true, status: ready`, and the stop file stopped the second worker. The API log shows 98 answers of 200, two of 202, one 409, and no 500s.

**Deviations from the design notes**
- Error codes: `invalid_request` (validation, wrong method, oversized body), `repo_empty`, and `upstream_unavailable` (GitHub unreachable or failing, 502) join the listed codes. GitHub's own rate limit is `rate_limited` with 503, ours 429. `not_found` says what in `details.resource`.
- `POST /repos` also returns the snapshot's `status`, and `job_id` is null for a ready snapshot built outside the queue (`repoatlas build`). It answers 202 even for a ready snapshot (the PRD's shape; `reused` tells). It takes `url` only: pinning a commit stays in the CLI.
- The build steps of the graph store became the registered stages, so `repoatlas build` and the worker get the same fingerprint (decision 0007, addendum; M03's Provides updated). Snapshots stored before keep the old fingerprint and are not reused: zod got snapshot 4 next to M03's snapshot 1. `Stage` also has `components`, the versions of the code it runs (extractors, grammar packages, graph and tier versions), which entered the fingerprint before too.
- Attempts are counted when an attempt starts, so a job whose lease expired comes back with `attempts + 1` at its next claim; a graceful release gives the attempt back. With `max_attempts = 3` the 300 s backoff is never used; it applies to higher limits.
- `jobs` has more columns than PRD §7.1: `snapshot_id` (cascades with its snapshot), `dedupe_key` (one queued or running build per snapshot, through a partial unique index), `worker_id` (part of the fencing token with `attempts`), `run_after` (backoff), `started_at`, and `finished_at`.
- A snapshot stays `building` while its job retries; only a job that fails for good marks it `failed`. A failed snapshot, or one whose build was lost, gets a new build on the next request.
- The worker builds from the GitHub metadata in the job's payload instead of asking GitHub again, and refuses a job whose fingerprint differs from its own pipeline's (failed at once, never retried).
- Checkpoints are JSON files in `data_dir/builds/<digest of repo, commit, fingerprint>`: a retry on another machine starts over.
- A stopping worker releases its job at the next stage boundary, and errors raised while it stops (console signals reach `git` too) release the job instead of failing it. A second Ctrl+C stops at once.
- Each attempt may run `job_timeout_s` (1 hour): past it the heartbeats stop and the attempt fails at its next check.
- Reads: kinds are typed, repeated parameters (`kinds=calls&kinds=imports`), not comma-separated. File paths with empty, `.`, or `..` segments get 422 before any lookup, and NUL characters and IDs or lines past their Postgres types get 422 (they were 500s from Postgres). A node comes as `{node, owners, degree}` and search as `{query, results}`. Symbol keys in paths need `EncodedKeyMiddleware`, because servers decode `%2F` before routing.
- Job and snapshot errors are shown as recorded for types written for people (ingestion errors, pipeline mismatches, the queue's own messages); other exceptions show only their type, since their messages can carry SQL, parameters, or paths.
- Not in the design notes: request bodies are capped at 64 KB, every response has an `X-Request-ID`, operation IDs are the route functions' names, and `GET /ready` returns 503 while the database is not at the code's migration.
- `frontend/` gained a `package.json` and `package-lock.json` only to pin `openapi-typescript` and its peer `typescript` 5.9.3; the web app will grow from them.

**Found and fixed at the gate.** The foundation tests still expected `0002_graph_store` as the newest migration; the first full run caught it (`3bc1f0f`).

**Open question.** No cancel endpoint: builds take seconds to a minute, and stopping a worker (Ctrl+C or the stop file) puts its job back in the queue. It went to the backlog, with rate limits shared across API instances and sweeping orphaned build directories.

**CI.** Not run yet: the branch has not been pushed.
