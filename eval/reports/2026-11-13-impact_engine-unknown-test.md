# Impact: unknown bucket and test files (test)

Parameters: max_depth 4, decay 0.4, min_score 0.05. Questions with real failures: 9.

- Confirmed failing tests: 704; ranked 30, in the unknown bucket only 1, missed 673.
- Unknown-bucket hit rate (failures found only there): 0.001; questions it helped: 1.
- Test-file level (mean over questions): precision 0.063, recall 0.333, F1 0.101.

| Question | failing | ranked | unknown only | missed | unknown sites | file P | file R |
|---|---|---|---|---|---|---|---|
| `click-impact-001` | 35 | 0 | 0 | 35 | 20 | 0.000 | 0.000 |
| `click-impact-003` | 346 | 1 | 0 | 345 | 0 | 0.364 | 1.000 |
| `click-impact-004` | 266 | 0 | 1 | 265 | 9 | 0.000 | 0.000 |
| `httpx-impact-001` | 1 | 0 | 0 | 1 | 0 | 0.000 | 0.000 |
| `httpx-impact-002` | 2 | 1 | 0 | 1 | 0 | 0.065 | 1.000 |
| `httpx-impact-003` | 5 | 0 | 0 | 5 | 65 | 0.000 | 0.000 |
| `remeda-impact-001` | 2 | 0 | 0 | 2 | 0 | 0.000 | 0.000 |
| `remeda-impact-003` | 19 | 0 | 0 | 19 | 0 | 0.000 | 0.000 |
| `remeda-impact-004` | 28 | 28 | 0 | 0 | 14 | 0.143 | 1.000 |
