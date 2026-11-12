# 0018 — Keep jina-embeddings-v2-base-code as the embedding model

**Status:** Accepted · **Date:** 2026-11-12

## Context

Decision 0009 chose local embeddings with fastembed and `jinaai/jina-embeddings-v2-base-code` as
the default. EVL-9 asks for a comparison on the dev split (retrieval recall@k, CPU throughput,
memory) and a recorded decision. CPU embedding is slow: the first build of zod embedded 5,320
chunks in 31 minutes (M06), so a faster model is tempting.

## Decision

Keep `jinaai/jina-embeddings-v2-base-code`.

**Comparison** (`repoatlas eval embeddings`, `eval/reports/2026-11-12-embeddings-dev.md`): vector
search only, exact cosine over every chunk of the five benchmark snapshots at the pinned commits
(13,200 chunks), 20 dev queries (10 lookup, 10 callers), each model in its own process, 16-CPU
AMD machine, Windows:

| Model | Dim | recall@5 | recall@10 | recall@20 | Chunks/s | Peak RSS |
|---|---|---|---|---|---|---|
| `jinaai/jina-embeddings-v2-base-code` (default) | 768 | 0.665 | 0.769 | 0.808 | 4.7 | 3,046 MB* |
| `BAAI/bge-small-en-v1.5` | 384 | 0.641 | 0.721 | 0.782 | 17.2 | 1,454 MB |

\* The default model's run reused its stored vectors, read from the database as text (about
10 million floats), which counts toward its peak; bge-small embedded every chunk itself. The
models' own footprints differ less than the peaks suggest.

Both find every lookup definition in the top 5. The difference is on callers, where the
relevant chunks are the calling functions rather than the named one: recall@10 0.538 for the
code model against 0.442 for bge-small. bge-small embeds about 3.7 times faster and has half
the vector size.

Callers, multihop, and impact questions are where retrieval is weakest already, so recall there
matters more than build time. Faster first builds stay in the backlog ("faster first-build
embeddings") as an engineering problem (batching, threads, a quantized model), not a reason to
switch models.

## Consequences

- No fingerprint change and no rebuilt snapshots.
- The sample is small (20 queries): the callers gap is suggestive, not significant. The command
  can be re-run with other candidates; a switch would change the build fingerprint and need
  every snapshot re-embedded.
