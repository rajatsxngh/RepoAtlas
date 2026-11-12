# 0017 — Edge confidences refit on calibration repos

**Status:** Accepted · **Date:** 2026-11-12

## Context

The tier table in `graph/tiers.py` (PRD §9.1, decision 0005) held the initial guesses. EVL-10 and
decision 0013 ask for confidences fitted on repos outside the benchmark and recorded here.
Confidences feed graph expansion in retrieval (expanded score = seed × confidence × 0.5^hop)
and, later, impact ranking, so they should mean "how often an edge like this is right".

## Decision

**Calibration repos** (recipes in `sandbox/calibration/`, never in the benchmark): pallets/flask
3.1.3 and pallets/itsdangerous 2.2.0 (Python, scip-python 0.6.8), supermacro/neverthrow v8.2.0
and gvergnaud/ts-pattern v5.9.2 (TypeScript, scip-typescript 0.6.2; tests indexed too). Their
SCIP indexes were built in the sandbox (`repoatlas sandbox build|scip`).

**Method** (`repoatlas eval calibrate-edges`, `repoatlas.evals.calibration`): RepoAtlas's graph is
built at the pinned commit and each edge is checked against the M09 SCIP facts. A `calls` edge
is confirmed when SCIP has a non-import reference to the callee inside the caller; a `tested_by`
edge when the test references the symbol (or, between files, a definition in it). An edge is
judged only when both files are in the SCIP index and SCIP knows the callee. New confidence =
Laplace-smoothed precision `(confirmed + 1) / (judged + 2)`, rounded to two places and capped at
0.99, for groups with at least 20 judged edges; T6's value is the total split across candidates,
so it is the smoothed per-edge precision times the mean candidate count.

**Result** (`eval/calibration/2026-11-12-fit.md`):

| Group | Judged | Precision [95% CI] | Old | New |
|---|---|---|---|---|
| calls T1 | 717 | 0.937 [0.917, 0.953] | 0.95 | **0.94** |
| calls T2 | 373 | 1.000 [0.990, 1.000] | 0.90 | **0.99** |
| calls T3 | 138 | 0.993 [0.960, 0.999] | 0.85 | **0.99** |
| calls T4 | 14 | 1.000 [0.785, 1.000] | 0.80 | 0.80 (too few) |
| calls T5 | 525 | 0.248 [0.213, 0.286] | 0.50 | **0.25** |
| calls T6 | 1,353 | 0.101 per edge, ~2.9 candidates | 0.30 | **0.29** (total) |
| tested_by, imports | 61 | 1.000 [0.941, 1.000] | 0.90 | **0.98** |
| tested_by, convention | 14 | 0.857 [0.601, 0.960] | 0.60 | 0.60 (too few) |

`TIERS_VERSION` is 2. Import, containment, inheritance, and route confidences are unchanged
(imports measured 0.964 [0.919, 0.985]; structural edges stay 1.0).

**Benchmark repos, measured only** (`eval/calibration/2026-11-12-benchmark.md`, not used for
fitting): T1 0.948, T2 0.684, T3 0.994, T4 1.000, T5 0.408, T6 0.151 per edge. T2 differs most:
on the benchmark repos, `alias.func()` resolution is wrong far more often (httpx's export list,
where scip-python itself mis-resolves 712 references, and zod's namespace style). The fitted
value is kept as the method says; benchmark reports show these numbers next to it.

## Consequences

- The graph fingerprint changes (`TIERS_VERSION`), so every snapshot is rebuilt; embeddings are
  reused by chunk hash. Fixture graph digests changed (py_small `78459fc4…`, ts_small
  `973ff3b8…`); parse digests did not.
- Known limits of the judge, which make precision a lower bound for T1: an inherited
  constructor (`CustomFlask(...)` linked to `Flask.__init__`) and calls in class bodies are
  judged wrong because SCIP attributes the reference differently. SCIP counts any reference,
  so an edge whose callee is only mentioned (not called) would be confirmed: an upper-bound
  effect for T5/T6. Four small repos are a thin sample (T4 and convention links were not
  refit); calibration can be re-run with more repos and a new decision.
- The tier order is no longer monotonic (T2, T3 above T1): the data, not the design, sets it.
