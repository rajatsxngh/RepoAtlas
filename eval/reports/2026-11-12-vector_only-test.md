# vector_only on benchmark (test)

Created 2026-11-12T20:04:40+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `vector_only` (answer), config `a470ae7c4c6d234d…` |
| Models | small: `claude-haiku-5-5`, large: `claude-sonnet-5-5` |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `5c6e98d365d8` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 3 |
| Cost | $6.17 in all ($6.17 new this run; system $0.0162 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| lookup | correct | 20 | 1.000 [1.000, 1.000] | 1.000 / 1.000 / 1.000 |
| callers | F1 | 20 | 0.541 [0.397, 0.678] | 0.560 / 0.514 / 0.549 |
| multihop | F1 | 15 | 0.169 [0.034, 0.343] | 0.157 / 0.192 / 0.160 |
| impact | static F1 | 15 | 0.220 [0.103, 0.354] | 0.225 / 0.231 / 0.205 |
| flow | must-cite recall | 15 | 0.357 [0.210, 0.510] | 0.360 / 0.360 / 0.350 |
| unanswerable | correct refusal | 5 | 1.000 [1.000, 1.000] | 1.000 / 1.000 / 1.000 |
| **all** | primary | 90 | 0.522 [0.439, 0.608] | 0.526 / 0.522 / 0.519 |

Other metrics (means):

- callers: precision 0.616, recall 0.532
- multihop: precision 0.190, recall 0.161
- impact: real_f1 0.199, real_precision 0.444, real_recall 0.180, real_recall_at_10 0.180, static_precision 0.369, static_recall 0.246
- flow: judge 0.651
- unanswerable: api_refusal 0.000

## Answers

- Citation validity before filtering: mean 0.975, pooled 0.962; dropped-claim rate 0.013.
- Claim support (946 claims checked): supported 0.906, partial 0.081, unsupported 0.013.
- Latency p50 4.2 s, p95 8.9 s.
- Outcomes: answered 221, refused_evidence 49.
- Refusals on answerable questions: evidence 34, API 0, budget 0 (of 255). Unanswerable: refused for evidence 15, API 0, answered 0 (of 15).

## Notes

- The claim-support checker and the flow judge use the same model family as the answering systems (a known bias); the judge is blind to system names, sees answers truncated to a fixed length, and its agreement with human labels is reported separately.
- Claim-support and judge results are exploratory: the reference labels in eval/labels/ were made by Claude at the owner's request, not by a person (agreement in eval/labels/*.agreement.md).
