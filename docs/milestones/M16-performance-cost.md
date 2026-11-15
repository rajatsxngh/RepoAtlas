# M16 — Performance & cost

**Type of work:** Optimization · **Size:** M (8 tasks) · **Status:** Done

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

- **Incremental builds:** `repoatlas.pipeline.incremental.build_incremental(previous_snapshot_id, new_sha) -> SnapshotId` — reuses everything that didn't change; every build (the build job and `repoatlas build`) does the same by itself when a ready snapshot of the same repo and fingerprint exists. Reused: parse results (`parse_cache`), the chunks of unchanged files, embeddings by chunk hash, and, with `REPOATLAS_INCREMENTAL_LINK=true`, the call resolution of files no invalidation rule touches (off by default: it gives the same graph but was measured slower than resolving again). A snapshot's stats say what was reused (`incremental`, `parse_cache`, `link`, `chunks`, `embeddings`). `backend/scripts/incremental_check.py` checks the equality on real commits.
- **Freshness:** `GET /api/v1/snapshots/{id}/freshness` → `{ snapshot_commit, remote_head, stale, commits_behind, default_branch, checked_at }` (using `git ls-remote`, cached for 5 minutes; `commits_behind` from GitHub's compare endpoint, `null` when unknown); a banner in the map ("This map is N commits behind") with a rebuild button that triggers an incremental build.
- **Cost tuning (build gate, decision 0024):** prompt-caching breakpoints on tool definitions, the system prompt, and an exploring conversation, checked in every mode's requests without a model; the tolerance rule as code. No routing, step cap, or evidence budget was changed: trying cheaper settings on dev is a live-pass item.
- **Cost report:** `repoatlas eval cost-report --before <reports> [--after <reports>]` → cost and latency per question and accuracy per mode, with 95% intervals on accuracy differences and a verdict per mode; the baseline of the reports measured so far is in `eval/reports/`. `repoatlas eval cache-estimate` prints an estimate (arithmetic) of what conversation caching saves.
- **Docs:** [`docs/performance.md`](../performance.md).

## Requirements

PRF-1, PRF-2, PRF-3, PRF-4, PRF-5, PRF-6

## Scope

**In:** baseline measurement, incremental rebuilds with correct invalidation, embedding reuse, freshness check and UI, prompt caching and routing/cap tuning, before/after report.

**Out:** new features, answer caching across users (in the backlog), changes to the benchmark.

## Design notes

### Change detection
- Compare the new inventory with the previous snapshot's files by path and blob OID: **unchanged**, **modified**, **added**, **deleted**, **renamed** (same OID, new path — treated as delete + add for symbol keys, since keys contain the path).

### What gets recomputed
As built (decision 0024; the plan's rules for re-resolving were not enough to keep the graph equal, and are replaced by the four below):

1. Parse: files whose parse result is not in the parse cache (keyed by path, blob, language, test flag, and parser versions).
2. Link: the passes that read the whole repository and are cheap run in full on every build: structure, import bindings and `imports` edges, class hierarchies and `inherits` edges, routes, test links, public markers, owners, `contains` edges, statistics.
3. **Call resolution** can be reused per file from the previous snapshot unless a rule invalidates the file (opt-in; by default every file's calls are resolved from the cached parse results, which is faster):
   - `changed`: the file is modified or added;
   - `imports`: its imports resolve differently than before (a digest per file, compared exactly; covers an added file that an unresolved import now reaches, a deleted or renamed target, a shadowing file, changed project configuration);
   - `dependency`: a file it can reach through imports and bindings, at any depth, is modified, added, or resolves its imports differently, or a path appeared in or vanished from a package it can reach;
   - `name_match`: one of its call sites has the name of a symbol defined in such a file, or in the old version of a modified or deleted file.
4. Chunks are copied for files with the same path and blob as in the previous snapshot and cut for the others; embeddings are reused whenever (chunk hash, model) already exists.
5. Owners are recomputed from history (cheap).

### Correctness check
- Scripted change sets on the fixture repos and one real repo (edit a function body, add a file, delete a file, rename a file, change an import, add a class that resolves a previously ambiguous call): the incremental graph must have the same digest as a full rebuild of the new commit.
- Also: one scenario per invalidation rule that gives a wrong graph when the rule is switched off; generated chains of random edits; and `REPOATLAS_INCREMENTAL_VERIFY=true`, which makes a build link in full as well and fail on a difference.

### Cost work
- Measure first: per mode, cost and latency on dev and test before any change (`PRF-1`).
- Prompt caching: place breakpoints on the system message and tool definitions, and on the last message of a conversation that the next call extends (an agent's exploring calls); confirm cache hits in traces (cache-read tokens > 0 on repeated static prefixes).
- Routing and caps: try cheaper settings (more `small`-role steps, smaller evidence budgets, fewer agent steps) on dev; keep a setting only if accuracy stays within tolerance.
- **Tolerance:** for each mode and question type, the paired difference in the primary metric must have a 95% interval whose lower bound is above −2 points; otherwise the cheaper setting is rejected.

## Tasks

- [x] **T1 — Baseline measurement (tooling and what exists).** `repoatlas eval cost-report --before …` prints cost and latency per question for each report; a baseline is written from the reports measured so far, labelled with what they measured. The per-mode baseline on dev and test is a live-pass item (decision 0024).
- [x] **T2 — Change detection.** Inventory diff with the five change classes; tests.
- [x] **T3 — Incremental parse and re-resolve.** Parse cache by content; per-file records; call resolution reused per file under the invalidation rules of decision 0024; a test per rule, each showing the graph is wrong without the rule.
- [x] **T4 — Reuse and equality checks.** Builds find their previous snapshot; `build_incremental`; chunk reuse; embedding reuse counted; scripted and generated change sets with digest equality against full rebuilds, through the store.
- [x] **T5 — Freshness.** Endpoint with caching; banner and rebuild button; Playwright test with mock mode.
- [x] **T6 — Prompt caching and tuning (build gate).** Cache breakpoints on tool definitions, the system prompt, and the growing conversation, checked in every mode's requests with `FakeLLM` and the cassettes; the tolerance rule as code. Cache reads in live traces and settings tried on dev are live-pass items (decision 0024).
- [x] **T7 — After measurement and report (tooling).** `repoatlas eval cost-report --before … --after …` with paired intervals and a verdict per mode, run end to end on stand-in reports. The live before/after report is a live-pass item (decision 0024).
- [x] **T8 — Documentation.** Performance notes in the docs; Results filled in.

## Exit criteria

- [x] For every scripted change set, the incremental graph's digest equals a full rebuild's digest (fixture repositories, generated edits, and two real commits of a real repository).
- [x] Embedding reuse is verified (cache hits counted in stage stats), and the chunks of an incremental build equal a full rebuild's.
- [x] The cost report prints before/after cost per question for every mode it is given, the paired accuracy difference with its 95% interval, and the tolerance verdict; every answering mode's requests carry the cache breakpoints (checked without a model).
- [x] The freshness banner shows on a stale snapshot and the rebuild button produces a fresh snapshot.

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

**Gate reached on 2026-11-15 (decision 0024). No model was called and nothing was spent.**
Incremental builds and the freshness check are built and measured in full. The cost half is a
build gate: its tooling is built and tested, and its numbers are pending in the live pass.
Evidence: [`docs/m16-gate-evidence.md`](../m16-gate-evidence.md). Notes for users:
[`docs/performance.md`](../performance.md).
CI run 43749499442 passed all five jobs on `d0f3301` (Linux full checks, Windows unit tests, API
types, frontend with end-to-end tests, docs lint).

What was built:

- **A parse cache** (`parse_cache`): parse results are kept by path, blob, and parser versions
  and shared by every snapshot, so a file that did not change is never parsed twice.
- **Builds that reuse their previous snapshot.** A build finds the newest ready snapshot of the
  same repository and configuration by itself (`build_incremental` names one), records how the
  commit differs from it (unchanged, modified, added, deleted, renamed), copies the chunks of
  unchanged files in the order a full build would write them, and embeds only chunks whose text
  is new. Its stats say what was reused.
- **Selective call resolution**, as planned and with rules that are actually sound
  (`link_graph(…, previous=…)`): correct, tested against a full link, and **off by default**,
  because measuring it showed it is slower than resolving again.
- **The freshness check**: `GET /snapshots/{id}/freshness` (`git ls-remote`, five-minute
  cache, commits behind from GitHub), a banner on stale maps, and a Rebuild button that starts
  an incremental build and opens the new map.
- **Cache breakpoints** on tool definitions, the system prompt, and an exploring conversation,
  in every answering mode's requests.
- **`repoatlas eval cost-report`**: cost and latency per question per mode, the paired accuracy
  difference with its 95% interval, and the tolerance verdict; `eval cache-estimate`; a
  baseline from the reports measured so far.

Numbers of the gate (2,531 passed, 1 skipped, 9 deselected in `poe check`):

| Check | Result |
|---|---|
| Incremental graph equals a full rebuild, scripted change sets | Pass: 26 scenarios in memory (Python and TypeScript), and 8 change sets built through the store with call reuse off and on (graph digest, chunks with their keyword index and ID order, embeddings) |
| Each invalidation rule is needed | Pass: with `imports`, `dependency`, or `name_match` switched off, its scenario's graph differs from the full one |
| Generated edits | Pass: 160 chains of four random edits on four repositories, always equal; all four rules tripped |
| Real commits (`scripts/incremental_check.py`) | Pass: 7 pairs across zod, flask, click, requests, httpx, and remeda (up to 237 files modified, 47 added, 19 deleted), all equal |
| Embedding reuse | Pass: after a one-function edit, 1 chunk embedded and the rest cached (stage stats); on zod, a build 12 commits back embedded 61 chunks and found 5,173 cached |
| Cost report | Pass on stand-ins: three modes through the eval runner before and after, paired per mode with intervals and verdicts; a mode that lost accuracy is rejected. **No measured before/after exists yet** |
| Cache breakpoints in every mode's requests | Pass, without a model: one-shot marks its system prompt; an agent's exploring calls carry three breakpoints and its final call two; the pipeline's stages each as their kind |
| Freshness banner and rebuild | Pass: API test with a real `git ls-remote` on a local origin; Playwright in mock mode (3) and against the real API and worker (1): stale map, Rebuild, incremental build, fresh map |
| Frontend | Lint and typecheck clean; 346 unit tests; Playwright 29 of 29 |

Measured on colinhacks/zod (about 500 files, 5,200 chunks), three consecutive real commits,
embeddings warm on both sides, stage timings in seconds (medians):

| Stage | Full rebuild | Incremental |
|---|---|---|
| parse | 2.6 | 0.5 |
| graph | 0.8 | 1.0 |
| chunk | 2.2 | 0.8 |
| fetch, store, embed, and the rest | 7.8 | 7.6 |
| **all stages** | **13.4** | **9.9** |

What was learned, including what did not work:

- **A rebuild is about a quarter faster, not several times faster.** Parsing and chunking are
  what incremental builds save (3.5 s of 13). Fetching the commit and writing the snapshot's
  rows are most of the rest and can't be skipped. The large saving, not re-embedding unchanged
  chunks, was already in place: a first build of zod spends 31 minutes embedding, a rebuild
  with nothing new spends one second.
- **Selective call resolution does not pay.** The planned rules (changed files and their
  direct reverse-import dependents) give wrong graphs: a method removed two imports away, a
  member added anywhere that name matching finds, a file that shadows an import. With rules
  that are sound, most files are invalidated on real change sets (zod across a release: 468
  of 503). And where files are kept, their calls must be read back from the database, which
  costs more than resolving them again: the graph stage took 1.1 s with every file's calls
  kept, against 1.0 s without. It ships opt-in (`REPOATLAS_INCREMENTAL_LINK`).
- **PRF-2 is met in a stricter form than written:** by default every file is re-resolved, not
  only changed files and their dependents, from cached parse results. The result equals a full
  rebuild either way.
- **A query plan can change the measurement.** The first chunk copy joined the new snapshot's
  nodes in SQL and took 0.8 s or 2.1 s for the same work, depending on table statistics that
  are stale right after a snapshot is loaded. It now maps node IDs in Python and is steady.
- **What prompt caching saves is not measured.** By arithmetic on the price table, marking an
  exploring conversation saves 40% to 70% of the exploring calls' input cost from six calls
  on, and costs about 7% more for an exploration of two calls. Those calls run on the small
  model, so the dollars are small.

What the gate does not show, and nothing here claims:

- **What an answer costs in agent or multi-agent mode**, or in one-shot mode on the current
  prompt (live-pass item 20). The only measured costs are the M10 runs of 2026-11-12:
  `hybrid_graphrag` $0.0220 per question and 4.9 s on the test split
  ([baseline](../../eval/reports/2026-11-15-cost-baseline.md)).
- **Whether the API reports cache reads for the new breakpoints** (item 21). The requests
  follow the documented shapes and have not been sent.
- **Whether any cheaper setting keeps accuracy inside the tolerance** (item 22). None was
  tried and none was adopted: routing, step caps, and evidence budgets are unchanged.
- **The before/after cost report** (item 23). The report tool works on stand-in reports, whose
  numbers measure scripts.

Found on the way:

- A stage before `store` that touches the database makes the stage unit tests wait on a
  database URL that points nowhere; the parse cache is switched off in their settings and
  never fails a build when the table can't be read.
- A two-call exploration pays slightly more with conversation caching than without; in the
  backlog with the measurement that would settle it.
- Text content sent as a string in one request and as a text block in the next would have
  relied on the API treating the two as the same cached prefix; requests that mark their
  conversation now use block form throughout.
