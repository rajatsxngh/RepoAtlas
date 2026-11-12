# M10 — Eval runner & baselines

**Type of work:** Measurement · **Size:** L (11 tasks) · **Status:** In progress

## Goal

Score any answering or impact system on the benchmark with honest statistics, and publish the first comparison of retrieval strategies — including where the graph doesn't help.

## Depends on

- **M09** — `questions.jsonl`, `splits.json` (frozen test split), question schema and ground truth, benchmark version and digest.
- **M08** — SCIP artifacts and recipes (also used to index calibration repos outside the benchmark).
- **M07** — `AnswerSystem` protocol and registry, `AnswerResult` with metrics, `LLMClient`, tracing (cost and latency per call), `CitationValidator`.
- **M06** — `Retriever` with modes `vector`, `keyword`, `hybrid`; `EvidenceRef`.
- **M03** — `GraphStore` (`locate` to map cited ranges to symbol keys; edges for calibration), `build_snapshot`.
- **M02** — edge provenance, `meta.tier`, and the tier table in `graph/tiers.py`.

## Provides

- **Registry** (`repoatlas.evals.registry`)
  - `register_answer_system(name, factory)` / `get_answer_system(name)` — systems registered here can be evaluated by name.
  - `ImpactTarget(symbol_key | diff)`, `ImpactPrediction(target, items: [ImpactItem(symbol_key, group: "test"|"public_api"|"caller"|"unknown", score, path?, evidence?)])`, `ImpactSystem` protocol `predict(snapshot_id, target) -> ImpactPrediction`, `register_impact_system(name, factory)`.
  - `register_suite(name, loader, scorers)` — named question suites with their own scorers; the default suite is `benchmark`.
- **Scorers** (`repoatlas.evals.scorers`): `lookup_correct`, `set_prf`, `recall_at_k`, `must_cite_recall`, `refusal_outcome`, plus aggregate metrics (pre-filter citation validity, dropped-claim rate, claim support, cost, latency p50/p95).
- **Claim-support checker** (`repoatlas.evals.support.check_claims(answer, evidence) -> [SupportLabel]`) — separate prompt on the `large` role; labels `supported | partial | unsupported`.
- **Flow judge** (`repoatlas.evals.judge`) — rubric scoring with bias controls; human-label agreement report.
- **Runner:** `repoatlas eval run --system <name> [--suite benchmark] [--split dev|test] [--runs 3] [--max-cost <usd>] [--concurrency 4] [--resume]`; results cached per (system config fingerprint, question ID, run index).
- **Comparison:** `repoatlas eval compare <report A> <report B>` → per-type differences with 95% paired bootstrap intervals.
- **Reports:** `eval/reports/<date>-<system>-<split>.json` (validated against `eval/report.schema.json`), plus `.md` and charts (`.png`).
- **Baseline systems:** `closed_book`, `bm25_only`, `vector_only`, `hybrid_graphrag` (registered answer systems).
- **Calibration:** `repoatlas eval calibrate-edges --repos <calibration recipes>` → per-provenance and per-tier precision; updated confidences written to `graph/tiers.py` through a decision record.
- **Embedding comparison:** `repoatlas eval embeddings --models <list> --split dev` → retrieval recall@k, throughput, memory.

## Requirements

EVL-1, EVL-2, EVL-3, EVL-4, EVL-5, EVL-6, EVL-7, EVL-8, EVL-9, EVL-10, EVL-11

## Scope

**In:** registry and protocols, scorers, claim-support checker, flow judge, runner, statistics, reports, baselines, calibration, embedding comparison, the first baseline report.

**Out:** new answering techniques, impact analysis itself, guardrail suites (the suite mechanism is generic).

## Design notes

### Mapping system output to ground truth
- Cited evidence references are mapped to symbol keys with `GraphStore.locate` on the evaluated snapshot (same repo and commit as the question).
- **Lookup:** correct if any valid citation's range contains the ground-truth definition line in the ground-truth path.
- **Lists (callers, multi-hop, static impact):** the predicted set is the set of symbol keys the answer cites as answers (claims tagged with a symbol); precision, recall, and F1 against the ground-truth set.
- **Real impact:** test-level precision, recall, and recall@k between predicted tests and confirmed failing tests.
- **Flow:** must-cite recall (cited symbols ∩ must-cite ÷ must-cite) as the primary metric; judge score as secondary.
- **Unanswerable:** correct only if refused for evidence reasons; API refusals are counted separately and never as correct.

### Fair baselines
- All answer baselines use the same `large` model, the same answer prompt, and the same evidence token budget. `closed_book` gets no evidence (it measures how much the model already knows about popular repos). `bm25_only` uses keyword mode, `vector_only` vector mode, `hybrid_graphrag` hybrid mode with graph expansion.

### Statistics
- Three runs per system and split; per-question means across runs.
- Paired bootstrap over questions (10,000 resamples, fixed seed) for differences between systems; 95% intervals reported per question type and overall.
- Sampling settings may not be fixable through the API, so run-to-run variance is part of the report.

### Tuning hygiene
- Parameters (retrieval k, fusion constants, expansion decay, refusal threshold) are tuned on dev only. Test-split runs happen only at checkpoints, and every test-split run is kept in `eval/reports/` (no cherry-picking).

### Judge and claim-support controls
- The flow judge sees the question, the reference outline, the must-cite list, and the answer — without the system name; when comparing two answers, their order is randomized; answers are truncated to a fixed length so verbosity doesn't win.
- About 50 human labels on dev questions (flow scores and claim-support labels) measure agreement (accuracy and Cohen's kappa). If agreement is low, judge and claim-support results are reported as exploratory.
- The judge and checker use the same model family as the answering systems; this is a known bias, mitigated by the controls above and stated in reports.

### Calibration
- Two or three calibration repos outside the benchmark get SCIP indexes through sandbox recipes. RepoAtlas edges on those repos are compared with SCIP references: precision per provenance and per tier. New tier confidences come from these numbers (never from benchmark repos) and are recorded in a decision. Benchmark reports show calibration results without refitting.

### Embedding comparison
- Candidates: the current default plus at least one alternative available locally (through fastembed or a custom model registration). Relevance comes from lookup and callers ground truth on dev. The decision (keep or switch) is recorded; switching changes the fingerprint and requires rebuilt snapshots.

### Cost control
- `--max-cost` stops the run when the traced spend reaches the cap; partial results are kept and the report marks them incomplete. Cached results cost nothing on re-runs.

### Report contents
- Benchmark version and digest, split, system name and config fingerprint, model IDs, embedding model, RepoAtlas commit, environment (OS, CPU, Python version), runs, per-type metrics with intervals, cost per question, latency percentiles, refusal breakdown, and the list of incomplete or errored items.

## Tasks

- [x] **T1 — Registry and protocols.** Answer and impact registries, `ImpactPrediction`, suite registration.
- [x] **T2 — Scorers.** Unit tests with synthetic predictions for each metric, including edge cases (empty answers, refusals, duplicate citations).
- [x] **T3 — Runner.** Caching, cost cap, concurrency, resume; tests with fake systems.
- [x] **T4 — Reports.** JSON schema, writers for JSON/markdown/charts, schema validation tests.
- [x] **T5 — Statistics and compare.** Paired bootstrap; `eval compare`; tests with known differences.
- [x] **T6 — Baseline systems.** `closed_book`, `bm25_only`, `vector_only`, `hybrid_graphrag` registered and tested with `FakeLLM`.
- [x] **T7 — Claim-support checker.** Prompt, labels, human-label set on dev, agreement report.
- [x] **T8 — Flow judge.** Rubric, bias controls, human-label agreement.
- [x] **T9 — Embedding comparison.** Run on dev; record the decision.
- [x] **T10 — Edge calibration.** Calibration repos, per-tier precision, updated tiers through a decision record.
- [x] **T11 — Baseline report.** All four baselines on the test split, 3 runs each; comparisons with intervals; summary in Results.

## Exit criteria

- [ ] Scorer, runner, report, and statistics tests pass.
- [ ] A test-split report exists for all four baselines (3 runs each), with per-type metrics and 95% intervals for hybrid GraphRAG vs vector-only.
- [ ] Judge and claim-support agreement with human labels is reported.
- [ ] The calibration table is produced and tier confidences are updated through a decision record.
- [ ] The embedding decision is recorded.

## Verification

```bash
cd backend
uv run pytest tests/unit/evals -q
uv run repoatlas eval run --system hybrid_graphrag --split dev --runs 1 --max-cost 3.00
uv run repoatlas eval run --system vector_only --split test --runs 3 --max-cost 10.00
uv run repoatlas eval compare eval/reports/<hybrid>.json eval/reports/<vector>.json
```

## Risks and open questions

- Evaluation cost: caching and cost caps keep spending predictable; dev runs use one run per system.
- The graph may not help on some question types; that result is published as is.
- Open: whether to add a human spot check of 10% of test-split answers to the final report.

## Results

_Filled in at the gate._
