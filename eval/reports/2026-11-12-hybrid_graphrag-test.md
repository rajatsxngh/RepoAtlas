# hybrid_graphrag on benchmark (test)

Created 2026-11-12T20:14:42+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `hybrid_graphrag` (answer), config `336d0e522fb75283…` |
| Models | small: `claude-haiku-5-5`, large: `claude-sonnet-5-5` |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `5c6e98d365d8` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 3 |
| Cost | $7.83 in all ($7.83 new this run; system $0.0220 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| lookup | correct | 20 | 1.000 [1.000, 1.000] | 1.000 / 1.000 / 1.000 |
| callers | F1 | 20 | 0.635 [0.501, 0.765] | 0.642 / 0.628 / 0.636 |
| multihop | F1 | 15 | 0.178 [0.046, 0.342] | 0.164 / 0.192 / 0.179 |
| impact | static F1 | 15 | 0.366 [0.228, 0.517] | 0.388 / 0.351 / 0.359 |
| flow | must-cite recall | 15 | 0.361 [0.268, 0.455] | 0.358 / 0.358 / 0.367 |
| unanswerable | correct refusal | 5 | 1.000 [1.000, 1.000] | 1.000 / 1.000 / 1.000 |
| **all** | primary | 90 | 0.570 [0.492, 0.648] | 0.572 / 0.567 / 0.570 |

Other metrics (means):

- callers: precision 0.695, recall 0.629
- multihop: precision 0.194, recall 0.193
- impact: real_f1 0.308, real_precision 0.593, real_recall 0.306, real_recall_at_10 0.306, static_precision 0.537, static_recall 0.380
- flow: judge 0.644
- unanswerable: api_refusal 0.000

## Answers

- Citation validity before filtering: mean 0.980, pooled 0.974; dropped-claim rate 0.012.
- Claim support (993 claims checked): supported 0.883, partial 0.099, unsupported 0.018.
- Latency p50 4.3 s, p95 8.9 s.
- Outcomes: answered 227, refused_evidence 43.
- Refusals on answerable questions: evidence 28, API 0, budget 0 (of 255). Unanswerable: refused for evidence 15, API 0, answered 0 (of 15).

## Notes

- The claim-support checker and the flow judge use the same model family as the answering systems (a known bias); the judge is blind to system names, sees answers truncated to a fixed length, and its agreement with human labels is reported separately.
- Claim-support and judge results are exploratory: the reference labels in eval/labels/ were made by Claude at the owner's request, not by a person (agreement in eval/labels/*.agreement.md).
