# closed_book on benchmark (test)

Created 2026-11-12T19:54:54+00:00. Complete.

| | |
|---|---|
| Benchmark | 1.0.0, questions sha256 `c121bf8d5914228d…`, test sha256 `163c2957919f0001…` |
| System | `closed_book` (answer), config `155836a7d7c1a018…` |
| Models | small: `claude-haiku-5-5`, large: `claude-sonnet-5-5` |
| Embedding model | `jinaai/jina-embeddings-v2-base-code` |
| RepoAtlas | 0.1.0 @ `5c6e98d365d8` |
| Environment | Windows 11, AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD (16 CPUs), Python 3.12.13 |
| Runs | 3 |
| Cost | $1.12 in all ($1.12 new this run; system $0.0036 per question per run) |

## Scores

Means over questions (each question averaged over its runs), with 95% bootstrap intervals.

| Type | Metric | Questions | Score [95% CI] | Per run |
|---|---|---|---|---|
| lookup | correct | 20 | 0.417 [0.217, 0.617] | 0.400 / 0.450 / 0.400 |
| callers | F1 | 20 | 0.158 [0.052, 0.286] | 0.145 / 0.178 / 0.153 |
| multihop | F1 | 15 | 0.000 [0.000, 0.000] | 0.000 / 0.000 / 0.000 |
| impact | static F1 | 15 | 0.050 [0.006, 0.110] | 0.042 / 0.076 / 0.033 |
| flow | must-cite recall | 15 | 0.170 [0.084, 0.271] | 0.172 / 0.160 / 0.179 |
| unanswerable | correct refusal | 5 | 1.000 [1.000, 1.000] | 1.000 / 1.000 / 1.000 |
| **all** | primary | 90 | 0.220 [0.150, 0.296] | 0.212 / 0.234 / 0.214 |

Other metrics (means):

- callers: precision 0.267, recall 0.121
- multihop: precision 0.000, recall 0.000
- impact: real_f1 0.000, real_precision 0.000, real_recall 0.000, real_recall_at_10 0.000, static_precision 0.163, static_recall 0.040
- flow: judge 0.611
- unanswerable: api_refusal 0.000

## Answers

- Citation validity before filtering: mean -, pooled -; dropped-claim rate -.
- Claim support (0 claims checked): supported -, partial -, unsupported -.
- Latency p50 2.8 s, p95 6.3 s.
- Outcomes: answered 115, refused_evidence 155.
- Refusals on answerable questions: evidence 140, API 0, budget 0 (of 255). Unanswerable: refused for evidence 15, API 0, answered 0 (of 15).

## Notes

- The claim-support checker and the flow judge use the same model family as the answering systems (a known bias); the judge is blind to system names, sees answers truncated to a fixed length, and its agreement with human labels is reported separately.
- Claim-support and judge results are exploratory: the reference labels in eval/labels/ were made by Claude at the owner's request, not by a person (agreement in eval/labels/*.agreement.md).
