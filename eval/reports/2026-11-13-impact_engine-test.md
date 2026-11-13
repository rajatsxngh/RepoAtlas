# impact_engine on benchmark (test)

Created 2026-11-13T00:29:30+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `impact_engine` (impact), config `c5c579aa52818343…` |
| Models | - |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `ffddaab27262` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 3 |
| Cost | $0.00 in all ($0.00 new this run; system $0.0000 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| impact | static F1 | 15 | 0.486 [0.289, 0.682] | 0.486 / 0.486 / 0.486 |
| **all** | primary | 15 | 0.486 [0.289, 0.682] | 0.486 / 0.486 / 0.486 |

Other metrics (means):

- impact: real_f1 0.186, real_precision 0.333, real_recall 0.167, real_recall_at_10 0.096, static_precision 0.612, static_recall 0.533

## Answers

- Citation validity before filtering: mean -, pooled -; dropped-claim rate -.
- Claim support (0 claims checked): supported -, partial -, unsupported -.
- Latency p50 0.1 s, p95 0.2 s.
- Outcomes: answered 45.
- Refusals on answerable questions: evidence 0, API 0, budget 0 (of 45). Unanswerable: refused for evidence 0, API 0, answered 0 (of 0).
