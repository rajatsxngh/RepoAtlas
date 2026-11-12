# hybrid_graphrag vs vector_only (benchmark test)

Difference = hybrid_graphrag - vector_only, per question (mean over runs), with 95% paired bootstrap intervals (10,000 resamples). * = the interval excludes 0.

| Type | Questions | vector_only | hybrid_graphrag | Difference [95% CI] | P(B better) |
|---|---|---|---|---|---|
| lookup | 20 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 |
| callers | 20 | 0.541 | 0.635 | +0.094 [+0.002, +0.219] * | 0.98 |
| multihop | 15 | 0.169 | 0.178 | +0.009 [-0.025, +0.047] | 0.66 |
| impact | 15 | 0.220 | 0.366 | +0.146 [+0.000, +0.306] * | 0.98 |
| flow | 15 | 0.357 | 0.361 | +0.004 [-0.075, +0.083] | 0.54 |
| unanswerable | 5 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 |
| **all** | 90 | 0.522 | 0.570 | +0.047 [+0.010, +0.090] * | 0.99 |
