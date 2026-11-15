# Cost baseline

Cost is the system's own spend per question per run (without the claim checker and the judge). Latency is seconds per question per run. Scores are each report's mean primary metric over its questions, with its own 95% bootstrap interval.

| Report | System | Split | Runs | Questions | Cost / question | Latency mean | p50 | p90 | Score [95% CI] | Cache reads |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-12-hybrid_graphrag-test.json | hybrid_graphrag | test | 3 | 90 | $0.0220 | 4.9 s | 4.3 s | 8.0 s | 0.570 [0.492, 0.648] | - |
| 2026-11-12-vector_only-test.json | vector_only | test | 3 | 90 | $0.0162 | 4.7 s | 4.2 s | 7.6 s | 0.522 [0.439, 0.608] | - |
| 2026-11-12-bm25_only-test.json | bm25_only | test | 3 | 90 | $0.0197 | 4.3 s | 3.5 s | 7.3 s | 0.441 [0.356, 0.528] | - |
| 2026-11-12-closed_book-test.json | closed_book | test | 3 | 90 | $0.0036 | 3.2 s | 2.8 s | 5.3 s | 0.220 [0.150, 0.296] | - |
| 2026-11-12-hybrid_graphrag-dev-2.json | hybrid_graphrag | dev | 1 | 40 | $0.0235 | 6.0 s | 4.9 s | 13.2 s | 0.643 [0.519, 0.765] | - |
| 2026-11-12-vector_only-dev-2.json | vector_only | dev | 1 | 40 | $0.0170 | 6.0 s | 4.2 s | 13.8 s | 0.629 [0.495, 0.752] | - |
| 2026-11-12-bm25_only-dev-2.json | bm25_only | dev | 1 | 40 | $0.0190 | 4.4 s | 3.4 s | 8.1 s | 0.518 [0.385, 0.648] | - |
| 2026-11-12-closed_book-dev-2.json | closed_book | dev | 1 | 40 | $0.0037 | 3.5 s | 2.9 s | 5.5 s | 0.333 [0.202, 0.471] | - |

Cache reads: the share of input tokens read from the prompt cache; `-` for reports that recorded no tokens.

## hybrid_graphrag on test (2026-11-12-hybrid_graphrag-test.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $7.83.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0191 | 3.0 s | 1.000 [1.000, 1.000] |
| callers | $0.0204 | 4.5 s | 0.635 [0.501, 0.765] |
| multihop | $0.0177 | 4.2 s | 0.178 [0.046, 0.342] |
| impact | $0.0284 | 7.7 s | 0.366 [0.228, 0.517] |
| flow | $0.0267 | 6.9 s | 0.361 [0.268, 0.455] |
| unanswerable | $0.0197 | 2.3 s | 1.000 [1.000, 1.000] |
| **all** | $0.0220 | 4.9 s | 0.570 [0.492, 0.648] |

## vector_only on test (2026-11-12-vector_only-test.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $6.17.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0128 | 2.8 s | 1.000 [1.000, 1.000] |
| callers | $0.0150 | 4.4 s | 0.541 [0.397, 0.678] |
| multihop | $0.0126 | 4.5 s | 0.169 [0.034, 0.343] |
| impact | $0.0188 | 6.4 s | 0.220 [0.103, 0.354] |
| flow | $0.0254 | 6.8 s | 0.357 [0.210, 0.510] |
| unanswerable | $0.0110 | 2.2 s | 1.000 [1.000, 1.000] |
| **all** | $0.0162 | 4.7 s | 0.522 [0.439, 0.608] |

## bm25_only on test (2026-11-12-bm25_only-test.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $6.76.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0172 | 2.7 s | 1.000 [1.000, 1.000] |
| callers | $0.0169 | 3.5 s | 0.220 [0.098, 0.350] |
| multihop | $0.0153 | 3.8 s | 0.171 [0.051, 0.318] |
| impact | $0.0192 | 5.9 s | 0.199 [0.078, 0.349] |
| flow | $0.0313 | 7.0 s | 0.316 [0.218, 0.414] |
| unanswerable | $0.0210 | 2.3 s | 1.000 [1.000, 1.000] |
| **all** | $0.0197 | 4.3 s | 0.441 [0.356, 0.528] |

## closed_book on test (2026-11-12-closed_book-test.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $1.12.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0024 | 2.3 s | 0.417 [0.217, 0.617] |
| callers | $0.0033 | 3.0 s | 0.158 [0.052, 0.286] |
| multihop | $0.0024 | 2.7 s | 0.000 [0.000, 0.000] |
| impact | $0.0044 | 3.7 s | 0.050 [0.006, 0.110] |
| flow | $0.0068 | 5.0 s | 0.170 [0.084, 0.271] |
| unanswerable | $0.0020 | 2.0 s | 1.000 [1.000, 1.000] |
| **all** | $0.0036 | 3.2 s | 0.220 [0.150, 0.296] |

## hybrid_graphrag on dev (2026-11-12-hybrid_graphrag-dev-2.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $1.21.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0230 | 3.4 s | 1.000 [1.000, 1.000] |
| callers | $0.0228 | 6.3 s | 0.699 [0.502, 0.864] |
| multihop | $0.0229 | 7.9 s | 0.100 [0.000, 0.300] |
| impact | $0.0285 | 9.0 s | 0.114 [0.001, 0.313] |
| flow | $0.0283 | 9.3 s | 0.535 [0.330, 0.795] |
| unanswerable | $0.0165 | 2.4 s | 1.000 [1.000, 1.000] |
| **all** | $0.0235 | 6.0 s | 0.643 [0.519, 0.765] |

## vector_only on dev (2026-11-12-vector_only-dev-2.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $0.84.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0138 | 2.8 s | 1.000 [1.000, 1.000] |
| callers | $0.0156 | 6.8 s | 0.604 [0.333, 0.857] |
| multihop | $0.0176 | 6.4 s | 0.057 [0.000, 0.171] |
| impact | $0.0265 | 10.7 s | 0.246 [0.085, 0.406] |
| flow | $0.0215 | 9.0 s | 0.520 [0.220, 0.800] |
| unanswerable | $0.0116 | 2.5 s | 1.000 [1.000, 1.000] |
| **all** | $0.0170 | 6.0 s | 0.629 [0.495, 0.752] |

## bm25_only on dev (2026-11-12-bm25_only-dev-2.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $0.76.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0204 | 2.6 s | 0.900 [0.700, 1.000] |
| callers | $0.0146 | 4.2 s | 0.313 [0.113, 0.520] |
| multihop | $0.0168 | 4.7 s | 0.080 [0.000, 0.240] |
| impact | $0.0199 | 7.5 s | 0.171 [0.029, 0.312] |
| flow | $0.0279 | 6.9 s | 0.468 [0.222, 0.760] |
| unanswerable | $0.0175 | 2.4 s | 1.000 [1.000, 1.000] |
| **all** | $0.0190 | 4.4 s | 0.518 [0.385, 0.648] |

## closed_book on dev (2026-11-12-closed_book-dev-2.json)

Models: large claude-sonnet-5-5, small claude-haiku-5-5. Measured 2026-11-12. Total spend $0.15.

| Type | Cost / question | Latency mean | Score [95% CI] |
|---|---|---|---|
| lookup | $0.0025 | 2.4 s | 0.500 [0.200, 0.800] |
| callers | $0.0036 | 3.5 s | 0.117 [0.000, 0.283] |
| multihop | $0.0026 | 3.1 s | 0.000 [0.000, 0.000] |
| impact | $0.0049 | 5.4 s | 0.160 [0.000, 0.480] |
| flow | $0.0077 | 5.5 s | 0.270 [0.050, 0.490] |
| unanswerable | $0.0022 | 2.7 s | 1.000 [1.000, 1.000] |
| **all** | $0.0037 | 3.5 s | 0.333 [0.202, 0.471] |

- Every number here was measured live on 2026-11-12 (the M10 runs), with the answer prompt of that day: before the M13 hardening, and before the cache breakpoints of decision 0024. hybrid_graphrag is the retrieval configuration the product's one-shot answers use.
- This is not the M16 baseline of the answering modes. Agent and multi-agent answers have never been run against a model, and one-shot answers have not been measured on the current prompt: those runs are live-pass item 20 (docs/live-pass.md) and are pending.
