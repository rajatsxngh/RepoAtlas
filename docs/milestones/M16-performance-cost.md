# M16 — Performance & cost

**Type of work:** Optimization · **Size:** M (8 tasks) · **Status:** Not started

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

- [ ] **T1 — Baseline measurement.** Cost and latency per mode on dev and test; report saved.
- [ ] **T2 — Change detection.** Inventory diff with the five change classes; tests.
- [ ] **T3 — Incremental parse and re-resolve.** Invalidation rules above; tests per rule.
- [ ] **T4 — Reuse and equality checks.** Chunk and embedding reuse; scripted change sets with digest equality against full rebuilds.
- [ ] **T5 — Freshness.** Endpoint with caching; banner and rebuild button; Playwright test with mock mode.
- [ ] **T6 — Prompt caching and tuning.** Verify cache hits; tune routing and caps on dev; decision record.
- [ ] **T7 — After measurement and report.** Re-run per mode; `eval cost-report`; check the tolerance rule.
- [ ] **T8 — Documentation.** Performance notes in the docs; Results filled in.

## Exit criteria

- [ ] For every scripted change set, the incremental graph's digest equals a full rebuild's digest.
- [ ] Embedding reuse is verified (cache hits counted in stage stats).
- [ ] The cost report shows before/after cost per question for every mode, with accuracy within the stated tolerance for every kept change.
- [ ] The freshness banner shows on a stale snapshot and the rebuild button produces a fresh snapshot.

## Verification

```bash
cd backend
uv run pytest tests/unit/pipeline/test_incremental.py tests/integration/pipeline -q
uv run repoatlas eval cost-report --before eval/reports/<before>/ --after eval/reports/<after>/
cd ../frontend && npx playwright test freshness
```

## Risks and open questions

- Invalidation bugs give wrong graphs silently; the digest-equality tests are the safety net and must cover every rule.
- Cheaper settings may trade accuracy on specific question types; the tolerance rule applies per type, not just overall.
- Open: whether to also cache identical questions per snapshot (in the backlog until measured as useful).

## Results

_Filled in at the gate._
