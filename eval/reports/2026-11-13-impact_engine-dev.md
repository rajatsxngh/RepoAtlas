# impact_engine on benchmark (dev)

Created 2026-11-13T00:27:05+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `impact_engine` (impact), config `c5c579aa52818343…` |
| Models | - |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `8716cd8ef280` (dirty) |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 1 |
| Cost | $0.00 in all ($0.00 new this run; system $0.0000 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| impact | static F1 | 5 | 0.635 [0.314, 0.886] | 0.635 |
| **all** | primary | 5 | 0.635 [0.314, 0.886] | 0.635 |

Other metrics (means):

- impact: real_f1 0.649, real_precision 0.995, real_recall 0.619, real_recall_at_10 0.356, static_precision 0.856, static_recall 0.671

## Answers

- Citation validity before filtering: mean -, pooled -; dropped-claim rate -.
- Claim support (0 claims checked): supported -, partial -, unsupported -.
- Latency p50 0.5 s, p95 0.5 s.
- Outcomes: answered 5.
- Refusals on answerable questions: evidence 0, API 0, budget 0 (of 5). Unanswerable: refused for evidence 0, API 0, answered 0 (of 0).
