# hybrid_graphrag vs closed_book (benchmark test)

Difference = hybrid_graphrag - closed_book, per question (mean over runs), with 95% paired bootstrap intervals (10,000 resamples). * = the interval excludes 0.

| Type | Questions | closed_book | hybrid_graphrag | Difference [95% CI] | P(B better) |
|---|---|---|---|---|---|
| lookup | 20 | 0.417 | 1.000 | +0.583 [+0.383, +0.783] * | 1.00 |
| callers | 20 | 0.158 | 0.635 | +0.477 [+0.313, +0.637] * | 1.00 |
| multihop | 15 | 0.000 | 0.178 | +0.178 [+0.046, +0.342] * | 1.00 |
| impact | 15 | 0.050 | 0.366 | +0.316 [+0.161, +0.475] * | 1.00 |
| flow | 15 | 0.170 | 0.361 | +0.191 [+0.106, +0.278] * | 1.00 |
| unanswerable | 5 | 1.000 | 1.000 | +0.000 [+0.000, +0.000] | 0.00 |
| **all** | 90 | 0.220 | 0.570 | +0.350 [+0.271, +0.430] * | 1.00 |
