# M11 — Impact analysis

**Type of work:** Graph algorithms and product feature · **Size:** L (10 tasks) · **Status:** In progress

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
  - `ImpactParams(max_depth=4, decay=0.6, min_score=0.05, limit=200, edge_kinds=(calls, inherits, imports, tested_by, routes_to))`
  - `ImpactReport(target, items: [ImpactItem(symbol_key, kind, group, score, depth, best_path: [PathStep(src, dst, kind, confidence)], evidence: [EvidenceRef])], unknown: [UnknownSite(site: EvidenceRef, callee_text, candidates, reason)], stats)`
- **`repoatlas.impact.diff.changed_symbols(snapshot_id, unified_diff) -> list[SymbolKey]`**.
- **API:** migration for `impact_runs`; `POST /api/v1/snapshots/{id}/impact` with `{ "symbol_key": … }` or `{ "diff": … }` → `ImpactReport`; regenerated OpenAPI and TypeScript types.
- **UI:** a `what_breaks` node action ("What breaks?") on function, method, and class nodes; `ImpactPanel` with groups (tests, public APIs, callers, unknown), ranked items with scores and paths; a scored highlight layer; clicking an item opens its evidence in `CodeViewer`.
- **Explanation:** `explain_impact(report) -> AnswerResult` — a cited, validated natural-language summary.
- **Registered impact system** `impact_engine` (adapter from `ImpactReport` to `ImpactPrediction`), plus test-split results against static and real ground truth.

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
- Parse a unified diff; take the new-side changed line ranges per file; map ranges to the innermost symbols with `locate`; deleted-only hunks map to the enclosing symbol at that position; the combined report merges per-symbol results (noisy-or across targets).

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
- [ ] **T6 — UI.** "What breaks?" action, `ImpactPanel`, scored highlight layer, evidence navigation; Playwright test.
- [ ] **T7 — Explanation.** `explain_impact` with validated citations; tests with `FakeLLM`.
- [ ] **T8 — Registration.** Adapter to `ImpactPrediction`; registered `impact_engine`.
- [ ] **T9 — Tuning on dev.** Parameter sweep; decision record with chosen values.
- [ ] **T10 — Test-split evaluation.** Report with intervals for static and real ground truth; summary in Results.

## Exit criteria

- [ ] Unit tests on synthetic graphs pass (decay, cycles, noisy-or aggregation, depth cap, unknown bucket).
- [ ] API tests and the Playwright "What breaks?" test pass.
- [ ] A test-split report gives precision, recall, and recall@k against both SCIP-derived static ground truth and sandbox failures, with 95% intervals.
- [ ] The chosen parameters are recorded in a decision.

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

_Filled in at the gate._
