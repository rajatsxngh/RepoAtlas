# impact_engine: static and real impact (test)

Parameters (decision 0019): max_depth 4, decay 0.4, min_score 0.05, limit 200. Means over questions with 95% bootstrap intervals (10,000 resamples). The engine is deterministic, so its three runner runs are identical.

| Ground truth | Metric | Questions | Mean [95% CI] |
|---|---|---|---|
| Static (SCIP) | precision | 15 | 0.612 [0.386, 0.837] |
| Static (SCIP) | recall | 15 | 0.533 [0.326, 0.741] |
| Static (SCIP) | F1 | 15 | 0.486 [0.289, 0.682] |
| Real (sandbox failures), test level | precision | 9 | 0.333 [0.000, 0.667] |
| Real (sandbox failures), test level | recall | 9 | 0.167 [0.000, 0.389] |
| Real (sandbox failures), test level | F1 | 9 | 0.186 [0.000, 0.444] |
| Real (sandbox failures), test level | recall@5 | 9 | 0.076 [0.000, 0.187] |
| Real (sandbox failures), test level | recall@10 | 9 | 0.096 [0.000, 0.230] |
| Real (sandbox failures), test level | recall@20 | 9 | 0.135 [0.000, 0.325] |

| Question | predicted | tests | static truth | failing | static P | static R | real P | real R | R@10 |
|---|---|---|---|---|---|---|---|---|---|
| `click-impact-001` | 1 | 0 | 2 | 35 | 1.000 | 0.500 | 0.000 | 0.000 | 0.000 |
| `click-impact-003` | 7 | 1 | 12 | 346 | 0.857 | 0.500 | 1.000 | 0.003 | 0.003 |
| `click-impact-004` | 0 | 0 | 35 | 266 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `httpx-impact-001` | 1 | 0 | 2 | 1 | 1.000 | 0.500 | 0.000 | 0.000 | 0.000 |
| `httpx-impact-002` | 5 | 1 | 5 | 2 | 1.000 | 1.000 | 1.000 | 0.500 | 0.500 |
| `httpx-impact-003` | 0 | 0 | 7 | 5 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `remeda-impact-001` | 0 | 0 | 3 | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `remeda-impact-003` | 0 | 0 | 17 | 19 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| `remeda-impact-004` | 28 | 28 | 28 | 28 | 1.000 | 1.000 | 1.000 | 1.000 | 0.357 |
| `requests-impact-001` | 27 | 24 | 1 | - | 0.037 | 1.000 | - | - | - |
| `requests-impact-003` | 19 | 13 | 19 | - | 1.000 | 1.000 | - | - | - |
| `requests-impact-004` | 19 | 3 | 46 | - | 0.947 | 0.391 | - | - | - |
| `zod-impact-001` | 3 | 0 | 1 | - | 0.333 | 1.000 | - | - | - |
| `zod-impact-002` | 4 | 0 | 4 | - | 1.000 | 1.000 | - | - | - |
| `zod-impact-003` | 2 | 2 | 18 | - | 1.000 | 0.111 | - | - | - |
