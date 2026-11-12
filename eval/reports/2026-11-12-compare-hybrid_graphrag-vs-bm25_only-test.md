# hybrid_graphrag vs bm25_only (benchmark test)

Difference = hybrid_graphrag - bm25_only, per question (mean over runs), with 95% paired bootstrap intervals (10,000 resamples). * = the interval excludes 0.

| Type | Questions | bm25_only | hybrid_graphrag | Difference [95% CI] | P(B better) |
|---|---|---|---|---|---|
| lookup | 20 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 |
| callers | 20 | 0.220 | 0.635 | +0.415 [+0.254, +0.583] * | 1.00 |
| multihop | 15 | 0.171 | 0.178 | +0.007 [-0.030, +0.040] | 0.66 |
| impact | 15 | 0.199 | 0.366 | +0.167 [+0.022, +0.336] * | 0.99 |
| flow | 15 | 0.316 | 0.361 | +0.045 [-0.038, +0.123] | 0.86 |
| unanswerable | 5 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 |
| **all** | 90 | 0.441 | 0.570 | +0.129 [+0.072, +0.190] * | 1.00 |
