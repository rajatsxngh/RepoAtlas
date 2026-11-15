# M16 — Performance & cost

**Type of work:** Optimization · **Size:** M (8 tasks) · **Status:** In progress

## Goal

Make rebuilds incremental and questions cheaper without losing accuracy, and publish the before/after numbers.

## Depends on

- **M15** — `MultiAgentAnswerSystem` (all answering modes now exist to measure).
- **M13** — traces with cost per span, `TracePanel`.
- **M10** — runner, compare, reports.
- **M06** — `chunk` and `embed` stages, `embeddings` cache keyed by (chunk hash, model).
- **M05** — web app (banner and rebuild button), API client.
- **M04** — stage registry, job runner, snapshot reuse, `POST /repos`.
- **M03** — schema, `save_snapshot`, `GraphStore`, blobs shared by OID.
- **M02** — `build_graph`, resolvers, `CodeGraph.digest()`.

## Provides

- **Incremental builds:** `repoatlas.pipeline.incremental.build_incremental(previous_snapshot_id, new_sha) -> SnapshotId` — reuses everything that didn't change; the build job uses it automatically when a ready snapshot of the same repo and fingerprint exists.
- **Freshness:** `GET /api/v1/snapshots/{id}/freshness` → `{ snapshot_commit, remote_head, stale }` (using `git ls-remote`, cached for 5 minutes); a banner in the map ("This map is N commits behind") with a rebuild button that triggers an incremental build.
- **Cost tuning:** verified prompt-caching breakpoints for static prompt parts and tool definitions; tuned routing, step caps, and evidence budgets per mode (chosen on dev, recorded in a decision).
- **Cost report:** `repoatlas eval cost-report --before <reports> --after <reports>` → cost per question and accuracy per mode, with 95% intervals on accuracy differences; published in `eval/reports/` and summarized in Results.

## Requirements

PRF-1, PRF-2, PRF-3, PRF-4, PRF-5, PRF-6

## Scope

**In:** baseline measurement, incremental rebuilds with correct invalidation, embedding reuse, freshness check and UI, prompt caching and routing/cap tuning, before/after report.

**Out:** new features, answer caching across users (in the backlog), changes to the benchmark.

## Design notes

### Change detection
- Compare the new inventory with the previous snapshot's files by path and blob OID: **unchanged**, **modified**, **added**, **deleted**, **renamed** (same OID, new path — treated as delete + add for symbol keys, since keys contain the path).

### What gets recomputed
1. Parse modified and added files.
2. **Re-resolve** (imports, calls, inheritance, test links) for:
   - modified and added files;
   - files whose previous imports pointed at modified, deleted, or renamed files (reverse-import dependents, from the previous graph);
   - files whose imports previously failed to resolve but could now resolve to an added file (unresolved imports matching the added module path).
3. Remove nodes and edges that belonged to deleted files; remove edges *into* changed files and recompute them from their sources.
4. Recompute directory nodes and aggregates.
5. Chunks are rebuilt for changed files; embeddings are reused whenever (chunk hash, model) already exists.
6. Owners are recomputed from history (cheap).

### Correctness check
- Scripted change sets on the fixture repos and one real repo (edit a function body, add a file, delete a file, rename a file, change an import, add a class that resolves a previously ambiguous call): the incremental graph must have the same digest as a full rebuild of the new commit.

### Cost work
- Measure first: per mode, cost and latency on dev and test before any change (`PRF-1`).
- Prompt caching: confirm cache hits in traces (cache-read tokens > 0 on repeated static prefixes); place breakpoints on the system message and tool definitions.
- Routing and caps: try cheaper settings (more `small`-role steps, smaller evidence budgets, fewer agent steps) on dev; keep a setting only if accuracy stays within tolerance.
- **Tolerance:** for each mode and question type, the paired difference in the primary metric must have a 95% interval whose lower bound is above −2 points; otherwise the cheaper setting is rejected.

## Tasks

- [x] **T1 — Baseline measurement (tooling and what exists).** `repoatlas eval cost-report --before …` prints cost and latency per question for each report; a baseline is written from the reports measured so far, labelled with what they measured. The per-mode baseline on dev and test is a live-pass item (decision 0024).
- [x] **T2 — Change detection.** Inventory diff with the five change classes; tests.
- [x] **T3 — Incremental parse and re-resolve.** Parse cache by content; per-file records; call resolution reused per file under the invalidation rules of decision 0024; a test per rule, each showing the graph is wrong without the rule.
- [x] **T4 — Reuse and equality checks.** Builds find their previous snapshot; `build_incremental`; chunk reuse; embedding reuse counted; scripted and generated change sets with digest equality against full rebuilds, through the store.
- [x] **T5 — Freshness.** Endpoint with caching; banner and rebuild button; Playwright test with mock mode.
- [ ] **T6 — Prompt caching and tuning (build gate).** Cache breakpoints on tool definitions, the system prompt, and the growing conversation, checked in every mode's requests with `FakeLLM` and the cassettes; the tolerance rule as code. Cache reads in live traces and settings tried on dev are live-pass items (decision 0024).
- [ ] **T7 — After measurement and report (tooling).** `repoatlas eval cost-report --before … --after …` with paired intervals and a verdict per mode, run end to end on stand-in reports. The live before/after report is a live-pass item (decision 0024).
- [ ] **T8 — Documentation.** Performance notes in the docs; Results filled in.

## Exit criteria

- [ ] For every scripted change set, the incremental graph's digest equals a full rebuild's digest (fixture repositories, generated edits, and two real commits of a real repository).
- [ ] Embedding reuse is verified (cache hits counted in stage stats), and the chunks of an incremental build equal a full rebuild's.
- [ ] The cost report prints before/after cost per question for every mode it is given, the paired accuracy difference with its 95% interval, and the tolerance verdict; every answering mode's requests carry the cache breakpoints (checked without a model).
- [ ] The freshness banner shows on a stale snapshot and the rebuild button produces a fresh snapshot.

### Live pass (pending; not part of this gate)

These need spending the owner has deferred (decisions 0020 and 0024). They are not checked here and must never be reported as passed until they are run:

- Cost and latency per answering mode on dev and test before any change.
- Cache reads confirmed in traces (cache-read tokens above zero on repeated static prefixes and on an agent's steps after the first).
- Cheaper routing, step caps, and evidence budgets tried on dev; a setting is kept only inside the tolerance.
- The before/after cost report per mode, with accuracy within the tolerance for every kept change.

## Verification

```bash
cd backend
uv run pytest tests/unit/pipeline/test_incremental.py tests/integration/pipeline -q
uv run pytest tests/unit/evals/test_cost_report.py tests/unit/llm/test_cache_breakpoints.py -q
uv run repoatlas eval cost-report --before eval/reports/<before>.json --after eval/reports/<after>.json
cd ../frontend && npx playwright test freshness
```

Live pass (needs an API key; costs money): see [`docs/live-pass.md`](../live-pass.md), items 20 to 23.

## Risks and open questions

- Invalidation bugs give wrong graphs silently; the digest-equality tests are the safety net and must cover every rule.
- Cheaper settings may trade accuracy on specific question types; the tolerance rule applies per type, not just overall.
- Open: whether to also cache identical questions per snapshot (in the backlog until measured as useful).

## Results

_Filled in at the gate._
