# hybrid_graphrag on benchmark (dev)

Created 2026-11-12T09:22:41+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `hybrid_graphrag` (answer), config `336d0e522fb75283…` |
| Models | small: `claude-haiku-5-5`, large: `claude-sonnet-5-5` |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `55ddd93cd370` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 1 |
| Cost | $1.21 in all ($0.00 new this run; system $0.0235 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| lookup | correct | 10 | 1.000 [1.000, 1.000] | 1.000 |
| callers | F1 | 10 | 0.699 [0.502, 0.864] | 0.699 |
| multihop | F1 | 5 | 0.100 [0.000, 0.300] | 0.100 |
| impact | static F1 | 5 | 0.114 [0.001, 0.313] | 0.114 |
| flow | must-cite recall | 5 | 0.535 [0.330, 0.795] | 0.535 |
| unanswerable | correct refusal | 5 | 1.000 [1.000, 1.000] | 1.000 |
| **all** | primary | 40 | 0.643 [0.519, 0.765] | 0.643 |

Other metrics (means):

- callers: precision 0.733, recall 0.710
- multihop: precision 0.100, recall 0.100
- impact: real_f1 0.000, real_precision 0.000, real_recall 0.000, real_recall_at_10 0.000, static_precision 0.467, static_recall 0.074
- flow: judge 0.700
- unanswerable: api_refusal 0.000

## Answers

- Citation validity before filtering: mean 0.988, pooled 0.988; dropped-claim rate 0.000.
- Claim support (145 claims checked): supported 0.883, partial 0.103, unsupported 0.014.
- Latency p50 4.9 s, p95 13.9 s.
- Outcomes: answered 32, refused_evidence 8.
- Refusals on answerable questions: evidence 3, API 0, budget 0 (of 35). Unanswerable: refused for evidence 5, API 0, answered 0 (of 5).

## Notes

- The claim-support checker and the flow judge use the same model family as the answering systems (a known bias); the judge is blind to system names, sees answers truncated to a fixed length, and its agreement with human labels is reported separately.
