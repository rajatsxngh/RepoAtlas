# vector_only on benchmark (dev)

Created 2026-11-12T09:24:31+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `vector_only` (answer), config `a470ae7c4c6d234d…` |
| Models | small: `claude-haiku-5-5`, large: `claude-sonnet-5-5` |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `55ddd93cd370` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 1 |
| Cost | $0.94 in all ($0.94 new this run; system $0.0170 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| lookup | correct | 10 | 1.000 [1.000, 1.000] | 1.000 |
| callers | F1 | 10 | 0.604 [0.333, 0.857] | 0.604 |
| multihop | F1 | 5 | 0.057 [0.000, 0.171] | 0.057 |
| impact | static F1 | 5 | 0.246 [0.085, 0.406] | 0.246 |
| flow | must-cite recall | 5 | 0.520 [0.220, 0.800] | 0.520 |
| unanswerable | correct refusal | 5 | 1.000 [1.000, 1.000] | 1.000 |
| **all** | primary | 40 | 0.629 [0.495, 0.752] | 0.629 |

Other metrics (means):

- callers: precision 0.640, recall 0.617
- multihop: precision 0.067, recall 0.050
- impact: real_f1 0.014, real_precision 0.333, real_recall 0.007, real_recall_at_10 0.007, static_precision 0.500, static_recall 0.199
- flow: judge 0.633
- unanswerable: api_refusal 0.000

## Answers

- Citation validity before filtering: mean 0.997, pooled 0.994; dropped-claim rate 0.000.
- Claim support (146 claims checked): supported 0.918, partial 0.068, unsupported 0.014.
- Latency p50 4.2 s, p95 16.0 s.
- Outcomes: answered 31, refused_evidence 9.
- Refusals on answerable questions: evidence 4, API 0, budget 0 (of 35). Unanswerable: refused for evidence 5, API 0, answered 0 (of 5).

## Notes

- The claim-support checker and the flow judge use the same model family as the answering systems (a known bias); the judge is blind to system names, sees answers truncated to a fixed length, and its agreement with human labels is reported separately.
