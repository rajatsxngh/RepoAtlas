# hybrid_graphrag vs impact_engine (benchmark test)

Difference = hybrid_graphrag - impact_engine, per question (mean over runs), with 95% paired bootstrap intervals (10,000 resamples). * = the interval excludes 0.

| Type | Questions | impact_engine | hybrid_graphrag | Difference [95% CI] | P(B better) |
|---|---|---|---|---|---|
| lookup | 0 | - | - | - | - |
| callers | 0 | - | - | - | - |
| multihop | 0 | - | - | - | - |
| impact | 15 | 0.486 | 0.366 | -0.120 [-0.355, +0.117] | 0.16 |
| flow | 0 | - | - | - | - |
| unanswerable | 0 | - | - | - | - |
| **all** | 15 | 0.486 | 0.366 | -0.120 [-0.355, +0.117] | 0.16 |

- Warning: 75 questions are scored in only one report; they are left out.
