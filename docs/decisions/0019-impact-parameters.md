# 0019 — Impact parameters tuned on dev

**Status:** Accepted · **Date:** 2026-11-13

## Context

The impact engine (M11) spreads a change backwards through the graph: a path scores the product of
`decay × edge confidence` over its steps, several paths combine with noisy-or, and the search stops
expanding nodes whose best score is below `min_score` or whose best path is `max_depth` steps long.
The milestone started from `max_depth=4`, `decay=0.6`, `min_score=0.05`, `limit=200`, and asks for
the values to be tuned on the dev split only (decision 0013) and recorded here. Edge confidences are
the calibrated ones of decision 0017.

## Decision

**Method** (`repoatlas impact sweep --split dev`, `repoatlas.impact.tuning`): every point of the
grid `max_depth ∈ {1…5}` × `decay ∈ {0.4, 0.5, …, 0.9}` × `min_score ∈ {0.01, 0.02, 0.05, 0.1, 0.2,
0.3}` (180 points) runs the engine on the 5 dev impact questions (one per benchmark repo) and is
scored with the runner's own scorer: static F1 against SCIP-derived impact sets, and test-level F1
against the tests that really failed in sandbox mutations (3 of the 5 questions have them). The
objective is the mean of the two means. Ties go to the smaller search (shallower, then faster
decay, then higher `min_score`). `limit` stays 200. No LLM is involved; the sweep costs nothing.

**Result** (`eval/reports/2026-11-13-impact_engine-sweep-dev.md`):

| | max_depth | decay | min_score | objective | static F1 | static P | static R | real F1 |
|---|---|---|---|---|---|---|---|---|
| Chosen | 4 | 0.4 | 0.05 | 0.642 | 0.635 | 0.856 | 0.671 | 0.649 |
| Start | 4 | 0.6 | 0.05 | 0.561 | 0.473 | 0.676 | 0.671 | 0.649 |

**Chosen defaults:** `ImpactParams(max_depth=4, decay=0.4, min_score=0.05, limit=200)`.

## Consequences

- The faster decay keeps recall and raises static precision: with T1 calls (0.94), a direct caller
  scores 0.38, two steps 0.14, three steps 0.05; weak guesses (T5/T6 edges) drop out after one step.
  In effect the search reaches about three resolved steps, matching how SCIP impact sets are built
  (callers up to depth 3, plus tests).
- The optimum is a plateau, not a point: `decay` and `min_score` trade off (`0.5/0.1`, `0.6/0.1`,
  and `0.9/0.2` score the same), and `max_depth` 3 to 5 differ by under 0.002. Five dev questions
  can't separate them; the test split, not the dev score, is the honest number.
- Weak edges into tests (a T6 call from a test, about 0.15) now fall below `min_score` two steps
  out, so tests reached only through ambiguous calls are not ranked. They stay visible when the
  call site is unresolved (the unknown bucket), but not when it resolved to several candidates.
- Re-run the sweep whenever edge confidences (decision 0017) or the traversal change; the engine's
  output version (`IMPACT_SYSTEM_VERSION`) is part of the system fingerprint, so evaluation
  results never mix parameter sets.
