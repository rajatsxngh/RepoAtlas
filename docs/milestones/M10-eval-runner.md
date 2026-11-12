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

- [x] Scorer, runner, report, and statistics tests pass.
- [x] A test-split report exists for all four baselines (3 runs each), with per-type metrics and 95% intervals for hybrid GraphRAG vs vector-only.
- [x] Judge and claim-support agreement with human labels is reported.
- [x] The calibration table is produced and tier confidences are updated through a decision record.
- [x] The embedding decision is recorded.

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

Gate checked on 2026-11-13 on branch `m10-eval-runner`, on Windows 11 with Postgres and Docker up.

**Tests.** In `backend/`, `uv run poe check` passes: 1,373 tests passed, 1 skipped (symlinks need extra privileges on Windows), 7 deselected (the `live` tests); ruff, mypy (344 files), and the docs lint pass too. `tests/unit/evals` has 92 tests covering scorers, mapping, runner (caching, cost cap, concurrency, resume), reports and their schema, statistics, baselines, the checker, the judge, labels and agreement, embeddings, and calibration. They use `FakeLLM` and synthetic data; no unit test calls an LLM.

**Baselines on the test split** (benchmark 1.0.0, test sha256 `163c2957…dd4cd08`; 90 questions, 3 runs each; Claude Sonnet 5.5 for answers, the checker, and the judge; jina-embeddings-v2-base-code; snapshots 19–23 built with `TIERS_VERSION` 2). Score = the type's primary metric, averaged per question over runs, with 95% bootstrap intervals. Reports: `eval/reports/2026-11-12-<system>-test.{json,md,png}`.

| Type (metric) | closed_book | bm25_only | vector_only | hybrid_graphrag |
|---|---|---|---|---|
| lookup (correct) | 0.417 | 1.000 | 1.000 | 1.000 |
| callers (F1) | 0.158 | 0.220 | 0.541 | 0.635 |
| multihop (F1) | 0.000 | 0.171 | 0.169 | 0.178 |
| impact (static F1) | 0.050 | 0.199 | 0.220 | 0.366 |
| flow (must-cite recall) | 0.170 | 0.316 | 0.357 | 0.361 |
| unanswerable (correct refusal) | 1.000 | 1.000 | 1.000 | 1.000 |
| **all** | 0.220 [0.150, 0.296] | 0.441 [0.356, 0.528] | 0.522 [0.439, 0.608] | 0.570 [0.492, 0.648] |
| cost per question per run | $0.004 | $0.020 | $0.016 | $0.022 |

**Hybrid GraphRAG vs vector-only** (paired bootstrap, 10,000 resamples; `eval/reports/2026-11-12-compare-hybrid_graphrag-vs-vector_only-test.md`): overall +0.047 [+0.010, +0.090]. The graph helps on callers (+0.094 [+0.002, +0.219]) and impact (+0.146 [+0.000, +0.306]). It does not measurably help on multihop (+0.009 [−0.025, +0.047]) or flow (+0.004 [−0.075, +0.083]), and lookup and unanswerable are at the ceiling for both. Against BM25 the overall gain is +0.129 [+0.072, +0.190]; against closed book, +0.350 [+0.271, +0.430].

**Other numbers** (test split):
- Real failing tests (12 impact questions with mutation ground truth): hybrid precision 0.593, recall 0.306; vector 0.444 / 0.180; BM25 0.519 / 0.244; closed book 0.
- Citation validity before filtering 0.97–0.99 and dropped-claim rate 0.3–1.3% for the three evidence systems. Claim support (checker): 88–91% supported.
- Latency p50 3.5–4.3 s, p95 8.7–8.9 s (closed book 2.8 / 6.3 s).
- Refusals: every unanswerable question was refused for evidence in every run by every system; no API or budget refusals. Answerable questions refused: hybrid 28 of 255, vector 34, BM25 60, closed book 140.
- Run-to-run variance is small: overall means per run for hybrid 0.572 / 0.567 / 0.570.
- Closed book got 42% of lookups right from memory: the model knows these popular repos, which is why the evidence systems are scored on cited code.

**Dev results** (40 questions, 1 run; `eval/reports/2026-11-12-<system>-dev.json`): hybrid 0.643, vector 0.629, BM25 0.518, closed book 0.333. On dev, hybrid vs vector was +0.014 [−0.060, +0.099]. The `-dev-2` reports re-ran the checker and judge after a cache fix; the vector and BM25 ones lost those calls to an empty API balance (recorded as notes), so the first dev reports stand for them.

**Judge and claim-support agreement** (`eval/labels/`). The owner delegated the reference labels to Claude, so they are not human labels. Results are reported as exploratory in every report.
- Claim support: 30 claims from the hybrid dev run, accuracy 0.867, Cohen's kappa 0.56.
- Flow judge: 19 answers from all four dev runs, quadratic kappa on totals 0.80. Per-criterion kappa: steps 0.73, coverage 0.72 (weighted 0.80), grounded 0.43 (weighted 0.64).

**Calibration** (decision 0017; `eval/calibration/2026-11-12-fit.md`). flask, itsdangerous, neverthrow, and ts-pattern were indexed with SCIP in the sandbox (`sandbox/calibration/`). Fitted tier confidences: T1 0.94, T2 0.99, T3 0.99, T5 0.25, T6 0.29 total, tested-by-imports 0.98; T4 and convention links kept their values (too few edges). On the benchmark repos, measured only (`2026-11-12-benchmark.md`), T2 precision is 0.68 rather than 1.00.

**Embedding decision** (decision 0018). Keep jina-embeddings-v2-base-code: recall@10 0.769 vs 0.721 for bge-small-en-v1.5, with callers 0.538 vs 0.442. bge-small is 3.7× faster (17.2 vs 4.7 chunks/s).

**Cost.** Live spend for M10 was about $25.80: dev runs $3.70 (including $0.43 of checker and judge redone after a cache fix), the T8 judge refill $0.05, and test runs $21.88 (closed book $1.12, vector $6.17, hybrid $7.83, BM25 $6.76). Every run stayed under its cap.

**Deviations and fixes found by the live runs**
- *Scoring:* a claim tagged with the question's own target (“`f` is applied on `dropdb`”) now counts the symbols it cites. Outputs are re-mapped from cached citations on every run, so the fix cost nothing.
- *Cost cap:* the first new item runs alone, and a cached answer reserves only its missing checker or judge calls.
- *Cache:* each system's checker and judge results now have their own files.
- *Embedding model:* it loads once under concurrent queries (M06 code).
- *Windows:* peak memory is now measured.
- *Refusal threshold:* the 0.02 retrieval-score threshold is a fusion score, so it applies to hybrid only. The single-mode baselines refuse before the call only when retrieval finds nothing, and all systems share the after-call refusal rules.
- *Closed book:* it needs its own prompt (the repo and commit, no evidence) and is scored on the symbols its claims name, since it can't cite.

**Open question from the plan.** No human spot check of test answers was made; the owner may add one before the launch post.
