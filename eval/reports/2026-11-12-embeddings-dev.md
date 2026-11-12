# Embedding models on dev (vector search only)

Snapshots: click 19, httpx 21, remeda 22, requests 20, zod 23. Machine: Windows AMD64 AMD64 Family 23 Model 96 Stepping 1, AuthenticAMD, 16 CPUs.

| Model | Dim | recall@5 | recall@10 | recall@20 | Chunks/s | Peak RSS (MB) | Load (s) |
|---|---|---|---|---|---|---|---|
| `jinaai/jina-embeddings-v2-base-code` | 768 | 0.665 | 0.769 | 0.808 | 4.7 | 3046 | 3.0 |
| `BAAI/bge-small-en-v1.5` | 384 | 0.641 | 0.721 | 0.782 | 17.2 | 1454 | 2.0 |

Recall by question type:

- `jinaai/jina-embeddings-v2-base-code`: callers @5 0.330, @10 0.538, @20 0.617; lookup @5 1.000, @10 1.000, @20 1.000 (20 queries, 13200 chunks)
- `BAAI/bge-small-en-v1.5`: callers @5 0.282, @10 0.442, @20 0.565; lookup @5 1.000, @10 1.000, @20 1.000 (20 queries, 13200 chunks)
