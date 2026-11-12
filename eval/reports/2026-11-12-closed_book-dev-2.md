# closed_book on benchmark (dev)

Created 2026-11-12T09:34:41+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `closed_book` (answer), config `155836a7d7c1a018…` |
| Models | small: `claude-haiku-5-5`, large: `claude-sonnet-5-5` |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `284309f72892` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 1 |
| Cost | $0.15 in all ($0.00 new this run; system $0.0037 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| lookup | correct | 10 | 0.500 [0.200, 0.800] | 0.500 |
| callers | F1 | 10 | 0.117 [0.000, 0.283] | 0.117 |
| multihop | F1 | 5 | 0.000 [0.000, 0.000] | 0.000 |
| impact | static F1 | 5 | 0.160 [0.000, 0.480] | 0.160 |
| flow | must-cite recall | 5 | 0.270 [0.050, 0.490] | 0.270 |
| unanswerable | correct refusal | 5 | 1.000 [1.000, 1.000] | 1.000 |
| **all** | primary | 40 | 0.333 [0.202, 0.471] | 0.333 |

Other metrics (means):

- callers: precision 0.200, recall 0.083
- multihop: precision 0.000, recall 0.000
- impact: real_f1 0.000, real_precision 0.000, real_recall 0.000, real_recall_at_10 0.000, static_precision 0.200, static_recall 0.133
- unanswerable: api_refusal 0.000

## Answers

- Citation validity before filtering: mean -, pooled -; dropped-claim rate -.
- Claim support (0 claims checked): supported -, partial -, unsupported -.
- Latency p50 2.9 s, p95 8.6 s.
- Outcomes: answered 15, refused_evidence 25.
- Refusals on answerable questions: evidence 20, API 0, budget 0 (of 35). Unanswerable: refused for evidence 5, API 0, answered 0 (of 5).

## Notes

- The claim-support checker and the flow judge use the same model family as the answering systems (a known bias); the judge is blind to system names, sees answers truncated to a fixed length, and its agreement with human labels is reported separately.
