# M11 — Impact analysis

**Type of work:** Graph algorithms and product feature · **Size:** L (10 tasks) · **Status:** Done

## Goal

Answer "What breaks if I change X?" with a ranked, explained list of affected tests, public APIs, and callers — plus an honest bucket of possible-but-unverified sites — and measure it against real test failures.

## Depends on

- **M10** — `ImpactSystem` protocol and registry, `ImpactTarget`, `ImpactPrediction`, impact scorers (set precision/recall/F1, recall@k, test-level scoring), runner, compare, reports.
- **M07** — `AnswerSystem` building blocks (`LLMClient`, prompt layout, `CitationValidator`, tracing) for cited explanations.
- **M05** — `GraphCanvas` highlight layers with scores, `NodeAction`, `revealNode`, `CodeViewer`.
- **M04** — FastAPI app, error model, rate limits, OpenAPI and TypeScript types.
- **M03** — `GraphStore` (`locate`, `unresolved_calls`, `get_node`), `SnapshotGraphCache` reverse adjacency, node `meta.public`.

## Provides

- **`repoatlas.impact.engine.ImpactEngine`**
  - `analyze(snapshot_id, target: SymbolTarget | DiffTarget, params: ImpactParams = defaults) -> ImpactReport`
  - `ImpactParams(max_depth=4, decay=0.4, min_score=0.05, limit=200, edge_kinds=(calls, inherits, imports, tested_by, routes_to))` — defaults tuned on dev (decision 0019; the starting decay was 0.6)
  - `ImpactReport(target, items: [ImpactItem(symbol_key, kind, group, score, depth, best_path: [PathStep(src, dst, kind, confidence)], evidence: [EvidenceRef])], unknown: [UnknownSite(site: EvidenceRef, callee_text, candidates, reason)], stats)`
- **`repoatlas.impact.diff.changed_symbols(snapshot_id, unified_diff) -> list[SymbolKey]`**.
- **API:** migration for `impact_runs`; `POST /api/v1/snapshots/{id}/impact` with `{ "symbol_key": … }` or `{ "diff": … }` → `ImpactReport`; regenerated OpenAPI and TypeScript types.
- **UI:** a `what_breaks` node action ("What breaks?") on function, method, and class nodes; `ImpactPanel` with groups (tests, public APIs, callers, unknown), ranked items with scores and paths; a scored highlight layer; clicking an item opens its evidence in `CodeViewer`.
- **Explanation:** `explain_impact(report) -> AnswerResult` — a cited, validated natural-language summary.
- **Registered impact system** `impact_engine` (adapter from `ImpactReport` to `ImpactPrediction`), plus test-split results against static and real ground truth.
- **Delivered details** (backward compatible with the above):
  - `ImpactEngine(data: ImpactData, *, params=None)` over `repoatlas.impact.data.ImpactData` (`StoreImpactData(GraphStore)` for stored snapshots, `MemoryImpactData(CodeGraph, files)` for tests and tools); `analyze(..., *, unknown=True)` (`False` skips the unknown bucket). `ImpactReport` also has `targets` (the changed symbols), `params`, and `group(name)`; `ImpactItem` also has `name`, `path`, and `best_score` (the best single path; `score` is the noisy-or). `PathStep.kind` adds `overrides` and `contains` to the edge kinds.
  - `DiffTarget(diff, side="old" | "new")`; `changed_symbols(snapshot_id, unified_diff, *, locator=None, side="old")` and `changed_symbols_in(locator, …)`; `parse_unified_diff`.
  - `POST /snapshots/{id}/impact` takes `{symbol_key | diff, side?, params?}` and answers the `ImpactReport` plus `run_id` and `trace_id` (`ImpactResponse`); each run is an `impact` trace with an `impact.analyze` span. `repoatlas.impact.runs` (`analyze_traced`, `save_impact_run`, `load_impact_run`), migration `0007_impact_runs`.
  - `GraphStore` gained `nodes_by_key`, `edges_at` (by adjacency position), `blob_oids`, and `texts_containing` (plain-substring search over stored file texts).
  - `ImpactExplainer(data, blobs, llm, *, prices, policy, sink)` with `explain(snapshot_id, report, budget)`; `explain_impact(report, *, snapshot_id, explainer, budget)`. An `impact_explain` trace; the M07 answer prompt, validator, and refusal kinds.
  - `repoatlas.impact.system` (`EngineImpactSystem`, `to_prediction`; `ctx.options`: `params`, `include_unknown`); `repoatlas.impact.tuning` (`sweep`, `score_point`, `Grid`); `repoatlas.impact.analysis` (`unknown_report`, `split_report`).
  - CLI: `repoatlas impact analyze --snapshot ID (--symbol KEY | --diff FILE) [--side]`, `repoatlas impact sweep --split dev`, `repoatlas impact report --split test`, `repoatlas impact unknown --split test`.
  - Frontend: `ApiClient.analyzeImpact`, `ImpactPanel` and `impactLayers(report)` (`src/components/impact/ImpactPanel.tsx`), mock mode's `POST /impact` (symbol keys only).

## Requirements

IMP-1, IMP-2, IMP-3, IMP-4, IMP-5, IMP-6, IMP-7, IMP-8, IMP-9

## Scope

**In:** reverse traversal and scoring, grouping, unknown bucket, diff targets, API, UI, explanations, registration, tuning on dev, test-split evaluation.

**Out:** agents, guardrail suites, deployment, running code (real failures come from existing sandbox artifacts).

## Design notes

### Traversal
- Start from the target symbol (or every symbol a diff touches).
- Follow reversed edges: `calls` (callee → caller), `inherits` (base → subclass; when the target is a method, subclass methods that override it are included), `imports` (when the target is module-level code or an exported symbol: its file → importing files), `tested_by` (production → test), `routes_to` (handler → route).
- Best-first search with a priority queue on score; a node's score from one path is `parent_score × decay × edge_confidence`; scores from several paths combine with noisy-or `1 − Π(1 − s_i)`; nodes below `min_score` or beyond `max_depth` are not expanded. Visited tracking makes it cycle-safe.
- Each item keeps its best path (highest single-path score) and evidence: the call site or import range of the last edge on that path.

### Groups
- **Tests:** `test` nodes and test files.
- **Public APIs:** `route` nodes and nodes with `meta.public = true`.
- **Callers:** everything else.
- Items are ranked by score within each group; the report also has one combined ranking (used for recall@k).

### Unknown bucket
- Unresolved or ambiguous call sites (`GraphStore.unresolved_calls`) whose callee's last name segment equals the target's name.
- String-literal references to the target's name in code (for example `getattr(obj, "name")` or registry tables), found with a keyword search restricted to string literals.
- Unknown sites are listed with their reason and are never folded into "safe".

### Diff targets
- Parse a unified diff; take the changed line positions per file (the old side by default: the snapshot is the code the diff applies to; `side="new"` for a snapshot that already contains it); map ranges to the innermost symbols with `locate`; deleted-only hunks map to the enclosing symbol at that position; the combined report merges per-symbol results (noisy-or across targets).

### Explanation
- The report's top items and their evidence become the evidence set for a short answer ("Changing X affects …"), validated with `CitationValidator`; refusal rules apply.

### Tuning and evaluation
- `max_depth`, `decay`, and `min_score` are tuned on the dev split against static ground truth and real failures; values are recorded in a decision.
- Test-split results: callers and static impact (set precision, recall, F1) and real impact (test-level precision, recall, recall@5/@10/@20), with 95% intervals; the unknown bucket's hit rate (how often confirmed failures appear only there) is reported separately.

## Tasks

- [x] **T1 — Traversal and scoring.** Best-first search, decay × confidence, noisy-or; tests on synthetic graphs (chain, diamond, cycle, depth cap).
- [x] **T2 — Groups and ranking.** Tests, public APIs, callers; combined ranking.
- [x] **T3 — Unknown bucket.** Unresolved-call matching and string-literal references; tests.
- [x] **T4 — Diff targets.** Unified-diff parsing and symbol mapping; tests with edits, additions, deletions.
- [x] **T5 — Report and API.** `ImpactReport` schema, `impact_runs` migration, endpoint, OpenAPI and TypeScript types.
- [x] **T6 — UI.** "What breaks?" action, `ImpactPanel`, scored highlight layer, evidence navigation; Playwright test.
- [x] **T7 — Explanation.** `explain_impact` with validated citations; tests with `FakeLLM`.
- [x] **T8 — Registration.** Adapter to `ImpactPrediction`; registered `impact_engine`.
- [x] **T9 — Tuning on dev.** Parameter sweep; decision record with chosen values.
- [x] **T10 — Test-split evaluation.** Report with intervals for static and real ground truth; summary in Results.

## Exit criteria

- [x] Unit tests on synthetic graphs pass (decay, cycles, noisy-or aggregation, depth cap, unknown bucket).
- [x] API tests and the Playwright "What breaks?" test pass.
- [x] A test-split report gives precision, recall, and recall@k against both SCIP-derived static ground truth and sandbox failures, with 95% intervals.
- [x] The chosen parameters are recorded in a decision.

## Verification

```bash
cd backend
uv run pytest tests/unit/impact tests/integration/api/test_impact.py -q
uv run repoatlas eval run --system impact_engine --split dev --runs 1
uv run repoatlas eval run --system impact_engine --split test --runs 3
cd ../frontend && npx playwright test impact
```

## Risks and open questions

- Name-matched calls inflate or miss impact; confidence weighting and the unknown bucket keep both visible, and the evaluation quantifies it.
- Test suites that exercise code through fixtures or dependency injection create real failures the graph can't see; those show up as recall gaps and are discussed in the report.
- Open: whether test-file-level predictions (instead of test-function level) should also be reported.

## Results

Gate checked on 2026-11-13 on branch `m11-impact-analysis`, on Windows 11 with Postgres and Docker up.

**Tests.** In `backend/`, `uv run poe check` passes: 1,454 tests passed, 1 skipped (symlinks need extra privileges on Windows), 7 deselected (the `live` tests); ruff, mypy (372 files), and the docs lint pass too. `tests/unit/impact` has 66 tests: traversal on synthetic graphs (chain decay, diamond noisy-or, parallel edges counted once, best path vs shortest path, self/mutual/long cycles, depth cap, `min_score`, per-kind edge directions, overrides through subclass chains, order independence), groups and ranking, the unknown bucket (unresolved calls, string literals, sites inside the target left out, the cap), diff parsing and mapping (edits, additions, deletion-only hunks, decorators, module-level code, old vs new numbering, new/binary/unknown files, `diff -u` output), the explainer with `FakeLLM` (validated citations, each refusal kind), the adapter and a scored run through the M10 runner, the sweep, and the analyses. `tests/integration/api/test_impact.py` (15 tests) checks the store-backed engine gives exactly the in-memory engine's report, the endpoint (symbol and diff targets, params, stored runs and traces, 404/409/422). Frontend: lint, typecheck, and 254 unit tests pass (5 for `ImpactPanel`, including script payloads rendered as text); Playwright `npx playwright test impact` passes against the real API (`e2e/app/shop-impact.spec.ts`, named to run after `map.spec.ts`, which needs the first build of the shop fixture) and in mock mode (`e2e/mock/impact.spec.ts`), and the full Playwright suite passes (12 of 12, including the 10,000-node stress test; median expansion 873 ms, slowest 1,475 ms). Docs lint passes.

**Tuning on dev** (decision 0019; `eval/reports/2026-11-13-impact_engine-sweep-dev.md`). 180 grid points (`max_depth` 1–5 × `decay` 0.4–0.9 × `min_score` 0.01–0.3) on the 5 dev impact questions, scored with the runner's scorer. Chosen: `max_depth=4, decay=0.4, min_score=0.05, limit=200` (objective 0.642: static F1 0.635, real F1 0.649), against 0.561 for the starting `decay=0.6` (static F1 0.473). The optimum is a plateau (`decay` and `min_score` trade off; depth 3–5 within 0.002). `repoatlas eval run --system impact_engine --split dev --runs 1` gives the same 0.635 (`eval/reports/2026-11-13-impact_engine-dev.md`). No LLM is involved; every impact run cost $0.

**Test split** (benchmark 1.0.0, test sha256 `163c2957…dd4cd08`; 15 impact questions, 9 with sandbox failures; snapshots 19–23; `eval run --system impact_engine --split test --runs 3`, identical runs since the engine is deterministic). Reports: `eval/reports/2026-11-13-impact_engine-test.{json,md,png}`, intervals in `2026-11-13-impact_engine-intervals-test.md` (95% bootstrap over questions, 10,000 resamples), unknown bucket in `2026-11-13-impact_engine-unknown-test.md`.

| Ground truth | Metric | Questions | Mean [95% CI] |
|---|---|---|---|
| Static (SCIP) | precision | 15 | 0.612 [0.386, 0.837] |
| Static (SCIP) | recall | 15 | 0.533 [0.326, 0.741] |
| Static (SCIP) | F1 | 15 | 0.486 [0.289, 0.682] |
| Real (sandbox), test level | precision | 9 | 0.333 [0.000, 0.667] |
| Real (sandbox), test level | recall | 9 | 0.167 [0.000, 0.389] |
| Real (sandbox), test level | F1 | 9 | 0.186 [0.000, 0.444] |
| Real (sandbox), test level | recall@5 / @10 / @20 | 9 | 0.076 / 0.096 / 0.135 |

(recall@5 [0.000, 0.187], @10 [0.000, 0.230], @20 [0.000, 0.325].)

- **Against the M10 answer systems** on the same 15 questions (`eval/reports/2026-11-13-compare-impact_engine-vs-hybrid_graphrag-test.md`): static F1 0.486 vs hybrid GraphRAG 0.366, a difference of +0.120 [−0.117, +0.355] for the engine (not significant with 15 questions). On real failures hybrid does better: real F1 0.308 vs 0.186, recall 0.306 vs 0.167. Hybrid's answers find tests by text (the test names the function it calls), which the graph misses where calls go through fixtures or values.
- **Unknown bucket.** Of 704 confirmed failing tests, 30 are ranked, 1 is found only in the unknown bucket (hit rate 0.001, one question helped), and 673 are missed. Unknown sites are listed for 4 of the 9 questions (up to 65 for one httpx target).
- **Test files** (the open question): scoring at the level of test files gives precision 0.063, recall 0.333, F1 0.101 (means over the 9 questions). Ranking test files lifts recall where a file's import links it, but most of a file's tests don't fail, so precision drops. Test-function level stays the primary metric; file level is in the unknown report.
- **Where recall goes** (per question in the intervals report): click's tests reach their targets through untyped pytest fixtures, so 645 of its 647 failures are invisible to the graph (the risk this milestone named; also why SCIP's static sets miss them). remeda passes implementations as values (`purry(sumImplementation, 1, args)`), so `remeda-impact-001` and `-003` have no `calls` edge to follow and predict nothing; `remeda-impact-004`, with direct calls, is perfect (28 of 28). Both gaps are in the backlog.

**Deviations.**
- Diff targets map the diff's old side by default (the snapshot is the code the diff applies to), not the new side the design notes first said; new-side numbering on an unchanged snapshot shifts every hunk after an insertion. `side="new"` covers a snapshot that already has the change.
- `explain_impact` takes the explainer (LLM client, blobs, data) as an argument instead of reading global settings, and has no endpoint or UI yet (it costs one `large` call per report); both are in the backlog.
- The adapter predicts symbols only (files, directories, and routes are left out), and unknown sites stay out of the prediction unless `include_unknown` is set: ground truth names symbols, and the unknown bucket is measured on its own.
- The runner now builds an LLM client only for answer systems, so impact runs need no API key.

**Cost.** $0: no live LLM call was made in M11 (the explanation is tested with `FakeLLM`).
