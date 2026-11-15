# M16 gate evidence

What was run for the M16 gate (decision
[0024](decisions/0024-m16-incremental-builds-cost-build-gate.md)) on 2026-11-15, on Windows 11 with
Postgres in Docker. **No model was called and nothing was spent.** Incremental builds and the
freshness check are measured in full; the cost half is a build gate, and its live runs are
pending in [live-pass.md](live-pass.md), items 20 to 23.

## Incremental linking equals a full link

```bash
cd backend
uv run pytest tests/unit/pipeline -q
```

203 tests passed.

| What | Where | Shown |
|---|---|---|
| Change detection | `tests/unit/pipeline/test_changes.py` (10) | Every path lands in exactly one of unchanged, modified, added, deleted, renamed; a rename pairs a gone path with a new one of the same blob; a copy of a file that stays is added; a file that becomes skipped is modified |
| Scripted change sets | `test_incremental.py` (26) | Edit a function body, add, delete, and rename a file (with and without its import), change an import, add a class that a name-matched call now finds, a second definer that makes a call ambiguous, remove a definer; TypeScript body edit, a re-export that moves, an added index file, a changed `tsconfig.json`; chains of builds; per-file records. In every case the graph linked on the previous build has the digest of the graph linked from nothing |
| One scenario per rule | `test_incremental.py` | With `imports`, `dependency`, or `name_match` switched off, the scenario's incremental graph **differs** from the full one; with every rule on it is equal. A stored edge whose target is gone is never restored |
| Generated edits | `test_incremental_random.py` (161) | 160 chains of four random edits (delete, rename, copy, append a definition that reuses a called name, cut lines, swap files, add an import, truncate, add a module, alias an import, add a constructor) on `py_small`, `ts_small`, and two more repositories, each step linked on the incremental result before it: always equal. The generator trips all four rules and reuses files (checked) |

### Real commits of real repositories

```bash
cd backend
uv run python scripts/incremental_check.py
```

Fetched from GitHub with git (no API, no model, no database). Call reuse is on in this check:
it is the part whose rules need checking.

```text
https://github.com/colinhacks/zod 19c40d207228 -> c7ec94d309ee
  files parsed: 522 -> 503; changes: {'unchanged': 328, 'modified': 177, 'added': 0, 'deleted': 19, 'renamed': 0}
  new graph: 5,185 nodes, 16,490 edges, 44,622 call sites
  calls resolved again: {'files_resolved': 468, 'files_reused': 35, 'by_rule': {'changed': 176, 'dependency': 112, 'imports': 2, 'name_match': 178}}
  parse 2.43 s; full link 0.94 s; incremental link 0.92 s
  digest bbf0d8cf2c9bcfc88493cdfe9c31e79edf41bf8922a13ecf90567cfd482acf79
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 503, 'by_rule': {}} in 0.71 s; equal: yes
https://github.com/colinhacks/zod 93186cab5340 -> c7ec94d309ee
  files parsed: 497 -> 503; changes: {'unchanged': 493, 'modified': 6, 'added': 6, 'deleted': 0, 'renamed': 0}
  new graph: 5,185 nodes, 16,490 edges, 44,622 call sites
  calls resolved again: {'files_resolved': 12, 'files_reused': 491, 'by_rule': {'changed': 12}}
  parse 2.62 s; full link 0.61 s; incremental link 0.65 s
  digest bbf0d8cf2c9bcfc88493cdfe9c31e79edf41bf8922a13ecf90567cfd482acf79
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 503, 'by_rule': {}} in 0.35 s; equal: yes
https://github.com/pallets/flask 4b8bde97d4fa -> 49943b5a9441
  files parsed: 83 -> 83; changes: {'unchanged': 58, 'modified': 25, 'added': 0, 'deleted': 0, 'renamed': 0}
  new graph: 1,915 nodes, 6,593 edges, 3,961 call sites
  calls resolved again: {'files_resolved': 78, 'files_reused': 5, 'by_rule': {'changed': 25, 'dependency': 51, 'name_match': 2}}
  parse 0.31 s; full link 0.23 s; incremental link 0.22 s
  digest acacf7ab52784685a028cc2dc24ffed07cf43d6297e48730b03d51d069f3154f
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 83, 'by_rule': {}} in 0.11 s; equal: yes
https://github.com/pallets/click 94c191ca6c95 -> cf19179e8e2f
  files parsed: 63 -> 91; changes: {'unchanged': 36, 'modified': 25, 'added': 30, 'deleted': 2, 'renamed': 0}
  new graph: 2,121 nodes, 10,556 edges, 6,757 call sites
  calls resolved again: {'files_resolved': 88, 'files_reused': 3, 'by_rule': {'changed': 55, 'dependency': 33}}
  parse 0.55 s; full link 0.35 s; incremental link 0.42 s
  digest ed8cdce53381da262fbcc65479a3d189978a679b26ffda38447cfd6aef21692a
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 91, 'by_rule': {}} in 0.29 s; equal: yes
https://github.com/psf/requests 6360477c5230 -> 6e83187b8feb
  files parsed: 36 -> 37; changes: {'unchanged': 14, 'modified': 22, 'added': 1, 'deleted': 0, 'renamed': 0}
  new graph: 845 nodes, 3,749 edges, 2,672 call sites
  calls resolved again: {'files_resolved': 32, 'files_reused': 5, 'by_rule': {'changed': 23, 'dependency': 6, 'name_match': 3}}
  parse 0.19 s; full link 0.08 s; incremental link 0.13 s
  digest 2ecffd7653da8380a07eadc712198916af6ea8561fdf509f23a638ba42c3b7c7
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 37, 'by_rule': {}} in 0.06 s; equal: yes
https://github.com/encode/httpx 83a85189c797 -> b5addb64f016
  files parsed: 60 -> 60; changes: {'unchanged': 34, 'modified': 26, 'added': 0, 'deleted': 0, 'renamed': 0}
  new graph: 1,307 nodes, 7,656 edges, 4,189 call sites
  calls resolved again: {'files_resolved': 55, 'files_reused': 5, 'by_rule': {'changed': 26, 'dependency': 28, 'name_match': 1}}
  parse 0.35 s; full link 0.25 s; incremental link 0.24 s
  digest d3790d6ce4cf9ed684d71a6ee2b54ae57e81b563e9b948782d4041a61cf96a4e
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 60, 'by_rule': {}} in 0.20 s; equal: yes
https://github.com/remeda/remeda 00958019b61d -> ec4d3f5dad11
  files parsed: 620 -> 659; changes: {'unchanged': 377, 'modified': 237, 'added': 47, 'deleted': 8, 'renamed': 0}
  new graph: 3,607 nodes, 13,766 edges, 28,722 call sites
  calls resolved again: {'files_resolved': 535, 'files_reused': 124, 'by_rule': {'changed': 284, 'dependency': 184, 'name_match': 67}}
  parse 1.79 s; full link 0.75 s; incremental link 0.72 s
  digest 4f7a01bbdd5fbb29f6850e89f855b9c6247a0825babf604af72bc1da372dc210
  incremental equals full: yes
  nothing changed: {'files_resolved': 0, 'files_reused': 659, 'by_rule': {}} in 0.67 s; equal: yes
```

All seven pairs are equal, and linking a commit on top of itself reuses every file.

## Incremental builds through the store

```bash
cd backend
uv run pytest tests/integration/pipeline tests/integration/jobs/test_build_job.py -q
```

53 tests passed.

| What | Where | Shown |
|---|---|---|
| Parse cache and records | `tests/integration/pipeline/test_parse_cache.py` (8) | A first build parses everything and fills the cache; a new commit parses only the changed file; another configuration shares the results; an unreadable row is parsed again; the cache can be off; a record per parsed file; stored calls read back per file |
| Scripted change sets, built | `test_incremental_build.py` (16 of 27) | Eight change sets, each built on top of a built snapshot, with call reuse off (the default) and on. The stored graph's digest equals `run_graph` of the commit; the stored chunks (node, lines, hash, header, text, keyword index, and ID order) equal the chunks cut from nothing; every chunk has an embedding; and after deleting both snapshots, a build with nothing to reuse stores the same graph and chunks |
| What was reused | `test_incremental_build.py` | After a one-function edit: parse cache 5 hits and 1 miss; 5 files' chunks copied and 1 chunk cut; **1 chunk embedded, the rest cached**; unchanged files are not cut again (spied); chains; `build_incremental` names the snapshot; reuse can be off; a previous snapshot without records still gives its chunks; a file whose nodes are not found again is cut; `incremental_verify` fails a build whose kept calls are wrong; chunk counts from rows equal the chunk objects' |
| The worker | `tests/integration/jobs/test_build_job.py` | A queued build of a new commit reuses the previous snapshot, and a retried attempt resumes from the graph checkpoint keeping what it was linked on |

### Measured on real commits

`repoatlas build` of consecutive real commits of colinhacks/zod into the development database,
embeddings warm for both sides (a third build embedded what was new first). Full: reuse and the
parse cache off, under another fingerprint. Stage timings in seconds, medians of three commits:

| Stage | Full rebuild | Incremental |
|---|---|---|
| resolve | 0.5 s | 0.5 s |
| fetch | 4.5 s | 5.0 s |
| inventory | 0.3 s | 0.2 s |
| parse | 2.6 s | 0.5 s |
| graph | 0.8 s | 1.0 s |
| store | 1.0 s | 0.9 s |
| chunk | 2.2 s | 0.8 s |
| embed | 1.0 s | 1.0 s |
| **all stages** | 13.4 s | 9.9 s |

The commits:

- `84e416fbf474`: {'added': 0, 'deleted': 0, 'renamed': 0, 'modified': 1, 'unchanged': 499}
- `1a16102a494b`: {'added': 0, 'deleted': 0, 'renamed': 0, 'modified': 3, 'unchanged': 497}
- `eab51ff3592b`: {'added': 0, 'deleted': 0, 'renamed': 0, 'modified': 1, 'unchanged': 499}

The same with call reuse on (`REPOATLAS_INCREMENTAL_LINK=true`, the default while this was
measured, before it was turned off), on three other commits:

| Stage | Full rebuild | Incremental |
|---|---|---|
| resolve | 0.4 s | 0.5 s |
| fetch | 4.2 s | 3.9 s |
| inventory | 0.2 s | 0.2 s |
| parse | 2.5 s | 0.5 s |
| graph | 0.8 s | 1.1 s |
| store | 1.1 s | 0.9 s |
| chunk | 2.2 s | 0.8 s |
| embed | 1.0 s | 1.0 s |
| **all stages** | 12.5 s | 8.9 s |

The graph stage is slower with call reuse (1.1 s against 0.8 s) even where every file is
reused, which is why it is opt-in. A first M16 build of an older commit embedded 61 new chunks
and found 5,173 cached; the build after it embedded none.

## Freshness

```bash
cd backend
uv run pytest tests/unit/ingest/test_freshness.py tests/integration/api/test_freshness.py tests/integration/api/test_e2e_fixtures.py -q
cd ../frontend && npx playwright test freshness
```

| What | Where | Shown |
|---|---|---|
| The check | `tests/unit/ingest/test_freshness.py` (19) | `git ls-remote` against a local repository returns the branch's head and follows it; odd branch names are refused before git runs; fresh means nothing is compared; a stale snapshot gets a count; answers are reused for five minutes per repository and branch; a count GitHub can't give is `null`; a failed lookup is raised and not cached; the compare endpoint is read |
| The endpoint | `tests/integration/api/test_freshness.py` (4) | Fresh, then (after a commit and five minutes) stale with the new head and the count; building the repository again gives a new snapshot that is fresh **and was built incrementally**; 404; 502 when the repository can't be asked; works while a snapshot is still building |
| The banner | `FreshnessBanner.test.tsx` (9), `mock/backend.test.ts` | Nothing for a current map; "This map is 3 commits behind main" with both commits; Rebuild follows the build and opens the new map; dismiss; a failed start is explained with a retry; nothing when the repository can't be asked |
| End to end, mock mode | `frontend/e2e/mock/freshness.spec.ts` (3) | A stale map shows the banner, Rebuild opens the head's map without one; a current map has none; dismiss |
| End to end, real API and worker | `frontend/e2e/app/freshness.spec.ts` (1) | A repository whose branch moves after its first build: the first map says 3 commits behind (a real `git ls-remote` on the local origin), Rebuild runs a real incremental build, the new map has no banner and shows the module the later commits added, and the old map still says it is behind |

Against the real repository, with the default checker (`git ls-remote` on GitHub and GitHub's
compare endpoint), for the zod snapshot in the development database:

```text
snapshot_commit c7ec94d309ee8acb25113e3db738b3e03816a424
remote_head     fef16cba0cf6668eb25ecc32fdfe2ce0a46f6f80
stale           True
commits_behind  139
default_branch  main
second call answered from the cache: True
```

## Cost: breakpoints and the report (build gate)

```bash
cd backend
uv run pytest tests/unit/llm/test_cache_breakpoints.py tests/integration/answers/test_cache_breakpoints_modes.py -q
uv run pytest tests/unit/evals/test_cost_report.py tests/integration/evals/test_cost_report_modes.py -q -s
```

| What | Where | Shown |
|---|---|---|
| Where requests are marked | `tests/unit/llm/test_cache_breakpoints.py` (14) | The last tool definition and the last system block; with `cache_conversation`, the last block of the last user message, on the request's copy; the caller's messages and tools are never changed; step by step the marker moves and the conversation, apart from markers, starts with the request before it; a call that may use tools is marked, a one-shot answer, a final call, and a repair turn are not; the setting turns it off |
| Every answering mode | `tests/integration/answers/test_cache_breakpoints_modes.py` (4) | Through the real `LLMClient` over the offline stand-in, on a built snapshot: one-shot sends one call marked on its system prompt only; the agent's exploring calls carry three breakpoints and its final call two; the pipeline's planner, writer, and verifier are plain, its explorers explore, its notes call keeps the explorers' prefix |
| The agent cassette | `tests/integration/agent/test_recorded_agent.py` (5) | Regenerated (hand-written, `"source": "docs"`) for the new request shape; replay still fails on any other change |
| The tolerance rule | `tests/unit/evals/test_cost_report.py` (17) | Built from the measured M10 report by changing costs and scores: cheaper with the same answers is kept; a five-point drop on one question type rejects the change though the overall holds; noise that could hide a drop is rejected; incomplete reports, other benchmark versions, and nothing to pair are undecided; pairing by system and split; folders; token counts; the estimate is the price table applied step by step |
| Three modes through the runner | `tests/integration/evals/test_cost_report_modes.py` (3) | `oneshot`, `agent`, and `multiagent` run through the eval runner twice with stand-ins and the report pairs them; a mode that lost accuracy is rejected (exit code 4) |

The report of the stand-in runs (**the numbers measure scripts, not the product**):

# Cost report: before and after

A change is **kept** only if, for every question type and overall, the lower bound of the 95% interval of the paired difference in the primary metric (after minus before, 10,000 bootstrap resamples over questions) is above -0.02. Cost is the system's own spend per question per run; cost and latency are compared on the questions both reports scored.

| System | Split | Cost before | Cost after | Change | Latency before | Latency after | Score before | Score after | Difference [95% CI] | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| agent | dev | $0.0044 | $0.0030 | -32% | 0.2 s | 0.2 s | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | **kept** |
| multiagent | dev | $0.0129 | $0.0085 | -34% | 0.2 s | 0.2 s | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | **kept** |
| oneshot | dev | $0.0040 | $0.0026 | -36% | 0.2 s | 0.2 s | 0.944 | 0.944 | +0.000 [+0.000, +0.000] | **kept** |

## agent on dev: kept

Before: 2026-11-15-cost_report_smoke-agent-dev.json (3 runs, 2026-11-15). After: 2026-11-15-cost_report_smoke-agent-dev.json (3 runs, 2026-11-15).

- Every question type and the overall difference have a lower bound above -0.020.

| Type | Questions | Before | After | Difference [95% CI] | Within tolerance | Cost before | Cost after | Latency before | Latency after |
|---|---|---|---|---|---|---|---|---|---|
| lookup | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0043 | $0.0029 | 0.1 s | 0.1 s |
| callers | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0046 | $0.0032 | 0.2 s | 0.2 s |
| **all** | 6 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0044 | $0.0030 | 0.2 s | 0.2 s |

Input tokens read from the prompt cache: 0% before, 75% after.

## multiagent on dev: kept

Before: 2026-11-15-cost_report_smoke-multiagent-dev.json (3 runs, 2026-11-15). After: 2026-11-15-cost_report_smoke-multiagent-dev.json (3 runs, 2026-11-15).

- Every question type and the overall difference have a lower bound above -0.020.

| Type | Questions | Before | After | Difference [95% CI] | Within tolerance | Cost before | Cost after | Latency before | Latency after |
|---|---|---|---|---|---|---|---|---|---|
| lookup | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0125 | $0.0082 | 0.2 s | 0.2 s |
| callers | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0133 | $0.0089 | 0.3 s | 0.3 s |
| **all** | 6 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0129 | $0.0085 | 0.2 s | 0.2 s |

Input tokens read from the prompt cache: 0% before, 75% after.

| Stage | Before | After |
|---|---|---|
| explorers | $0.0009 | $0.0008 |
| planner | $0.0040 | $0.0026 |
| verifier | $0.0040 | $0.0026 |
| writer | $0.0040 | $0.0026 |

## oneshot on dev: kept

Before: 2026-11-15-cost_report_smoke-oneshot-dev.json (3 runs, 2026-11-15). After: 2026-11-15-cost_report_smoke-oneshot-dev.json (3 runs, 2026-11-15).

- Every question type and the overall difference have a lower bound above -0.020.

| Type | Questions | Before | After | Difference [95% CI] | Within tolerance | Cost before | Cost after | Latency before | Latency after |
|---|---|---|---|---|---|---|---|---|---|
| lookup | 3 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | yes | $0.0040 | $0.0026 | 0.2 s | 0.2 s |
| callers | 3 | 0.889 | 0.889 | +0.000 [+0.000, +0.000] | yes | $0.0040 | $0.0026 | 0.2 s | 0.2 s |
| **all** | 6 | 0.944 | 0.944 | +0.000 [+0.000, +0.000] | yes | $0.0040 | $0.0026 | 0.2 s | 0.2 s |

Input tokens read from the prompt cache: 0% before, 75% after.

- Stand-in systems: these numbers measure scripts, not the product.

The baseline of what has been measured so far is
[`eval/reports/2026-11-15-cost-baseline.md`](../eval/reports/2026-11-15-cost-baseline.md): the
M10 runs of 2026-11-12, on the prompt of that day. The live test for cache reads
(`tests/live/test_prompt_cache_live.py`) was written and not run.

## The whole suite

```bash
cd backend && uv run poe check
cd ../frontend && npm run lint && npm run typecheck && npm test && npx playwright test
python scripts/check_milestones.py
scripts/gen_api_types.sh --check && (cd backend && uv run repoatlas eval schema --check)
```

| Check | Result |
|---|---|
| `uv run poe check` (ruff, mypy on `src` and `tests`, pytest, docs lint) | 2,531 passed, 1 skipped, 9 deselected (the live tests), in 19 min 17 s |
| Frontend lint and typecheck | clean |
| Frontend unit tests | 346 passed |
| Playwright (real API and worker, and mock mode) | 29 of 29 |
| Docs lint | 18 milestones, 165 requirements |
| API types and report schema | current |
