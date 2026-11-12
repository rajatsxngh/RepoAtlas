"""Embedding model comparison (EVL-9): retrieval recall@k on dev, CPU throughput, memory.

For each candidate model, in its own process (so peak memory belongs to that model alone):

1. **Throughput** — embed a fixed, seeded sample of chunks (the same sample for every model) and
   time it: chunks per second on this CPU, with the thread count recorded.
2. **Recall@k** — embed every chunk of the dev repos' snapshots (or reuse the stored vectors
   when the database already has them for this model), embed each dev ``lookup`` and
   ``callers`` question as a query, rank chunks by cosine similarity (exact search, like
   pgvector's), and measure which ground-truth symbols appear among the top-k chunks' symbols:
   the lookup definition, or the callers' answer set.
3. **Memory** — the process's peak resident memory after loading the model and embedding.

Vector search alone is measured (``vector_only`` retrieval), since that is what the embedding
model changes. The decision to keep or switch the default is recorded in a decision record;
switching changes the build fingerprint and needs rebuilt snapshots.
"""

from __future__ import annotations

import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from pydantic import BaseModel
from sqlalchemy import Engine, text

from repoatlas.bench.schema import AnswerSetTruth, LookupTruth
from repoatlas.evals.registry import EvalItem
from repoatlas.retrieval.embed import Embedder

KS = (5, 10, 20)
THROUGHPUT_SAMPLE = 400
SEED = 11


class ChunkRow(BaseModel):
    chunk_hash: str
    node_key: str
    text: str  # what the embed stage embeds: header, newline, text


class ModelResult(BaseModel):
    model: str
    dim: int
    threads: int | None
    queries: int
    chunks: int
    reused_stored_vectors: bool
    recall_at_k: dict[str, float]  # "5" → mean recall
    recall_by_type: dict[str, dict[str, float]]
    throughput_chunks: int
    throughput_seconds: float
    chunks_per_second: float
    peak_rss_mb: float | None
    load_seconds: float


class EmbeddingComparison(BaseModel):
    split: str
    snapshots: dict[str, int]
    machine: str
    cpu_count: int | None
    results: list[ModelResult]


def relevant_keys(item: EvalItem) -> set[str]:
    truth = item.truth
    if isinstance(truth, LookupTruth):
        return {truth.definition.symbol_key}
    if isinstance(truth, AnswerSetTruth):
        return {a.symbol_key for a in truth.answers}
    return set()


def recall_items(items: Sequence[EvalItem]) -> list[EvalItem]:
    return [item for item in items if item.type in ("lookup", "callers")]


def ranked_keys(
    query: np.ndarray, matrix: np.ndarray, keys: Sequence[str], limit: int
) -> list[str]:
    """Node keys of the chunks nearest to ``query`` (cosine), deduplicated, best first."""
    scores = matrix @ query
    order = np.argsort(-scores, kind="stable")
    out: list[str] = []
    for index in order:
        key = keys[int(index)]
        if key not in out:
            out.append(key)
            if len(out) >= limit:
                break
    return out


def recall_at(found: Sequence[str], relevant: set[str], k: int) -> float:
    return len(set(found[:k]) & relevant) / len(relevant) if relevant else 0.0


def peak_rss_mb() -> float | None:
    """This process's peak resident memory (Windows working set, or ``ru_maxrss``)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            windll = getattr(ctypes, "windll")  # noqa: B009  (Windows only)
            handle = windll.kernel32.GetCurrentProcess()
            ok = windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            return float(counters.PeakWorkingSetSize) / 2**20 if ok else None
        resource = __import__("resource")
        peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return peak / 2**20 if sys.platform == "darwin" else peak / 1024
    except Exception:
        return None


def load_chunk_rows(engine: Engine, snapshot_id: int) -> list[ChunkRow]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.chunk_hash, n.symbol_key, c.header, c.text FROM chunks c "
                "JOIN nodes n ON n.id = c.node_id WHERE c.snapshot_id = :s ORDER BY c.id"
            ),
            {"s": snapshot_id},
        ).all()
    return [
        ChunkRow(chunk_hash=r.chunk_hash, node_key=r.symbol_key, text=f"{r.header}\n{r.text}")
        for r in rows
    ]


def stored_vectors(engine: Engine, snapshot_id: int, model: str) -> dict[str, np.ndarray] | None:
    """The stored vectors of the snapshot's chunks for ``model``, or ``None`` if any is missing."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT DISTINCT e.chunk_hash, e.vector::text AS v FROM embeddings e "
                "JOIN chunks c ON c.chunk_hash = e.chunk_hash "
                "WHERE c.snapshot_id = :s AND e.model = :m"
            ),
            {"s": snapshot_id, "m": model},
        ).all()
        needed = connection.execute(
            text("SELECT count(DISTINCT chunk_hash) FROM chunks WHERE snapshot_id = :s"),
            {"s": snapshot_id},
        ).scalar_one()
    if len(rows) < needed:
        return None
    return {r.chunk_hash: np.array(json.loads(r.v), dtype=np.float32) for r in rows}


def _matrix(vectors: Sequence[Sequence[float]] | Sequence[np.ndarray]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.asarray(matrix / np.where(norms == 0, 1, norms), dtype=np.float32)


def throughput_sample(chunks: dict[str, list[ChunkRow]], n: int, seed: int) -> list[str]:
    texts = sorted({row.text for rows in chunks.values() for row in rows})
    rng = random.Random(seed)
    return rng.sample(texts, min(n, len(texts)))


def evaluate_model(
    embedder: Embedder,
    items: Sequence[EvalItem],
    chunks: dict[str, list[ChunkRow]],  # repo ID → chunks
    *,
    stored: dict[str, dict[str, np.ndarray]] | None = None,  # repo ID → hash → vector
    threads: int | None = None,
    sample_size: int = THROUGHPUT_SAMPLE,
    ks: Sequence[int] = KS,
) -> ModelResult:
    started = time.perf_counter()
    embedder.embed_query("warm up")  # loads the model
    load_seconds = time.perf_counter() - started
    sample = throughput_sample(chunks, sample_size, SEED)
    started = time.perf_counter()
    embedder.embed_documents(sample)
    seconds = time.perf_counter() - started

    queries = recall_items(items)
    per_type: dict[str, dict[int, list[float]]] = {}
    overall: dict[int, list[float]] = {k: [] for k in ks}
    reused = True
    total_chunks = 0
    for repo_id, rows in sorted(chunks.items()):
        repo_queries = [q for q in queries if q.repo_id == repo_id]
        if not repo_queries:
            continue
        total_chunks += len(rows)
        vectors_by_hash = (stored or {}).get(repo_id)
        if vectors_by_hash is None:
            reused = False
            unique = sorted({row.chunk_hash: row.text for row in rows}.items())
            embedded = embedder.embed_documents([t for _, t in unique])
            vectors_by_hash = {h: np.asarray(v) for (h, _), v in zip(unique, embedded, strict=True)}
        matrix = _matrix([vectors_by_hash[row.chunk_hash] for row in rows])
        keys = [row.node_key for row in rows]
        for item in repo_queries:
            query = _matrix([embedder.embed_query(item.text)])[0]
            found = ranked_keys(query, matrix, keys, max(ks))
            relevant = relevant_keys(item)
            for k in ks:
                value = recall_at(found, relevant, k)
                overall[k].append(value)
                per_type.setdefault(item.type, {kk: [] for kk in ks})[k].append(value)
    return ModelResult(
        model=embedder.model_name,
        dim=embedder.dim,
        threads=threads,
        queries=len(queries),
        chunks=total_chunks,
        reused_stored_vectors=reused,
        recall_at_k={str(k): statistics.fmean(v) if v else 0.0 for k, v in overall.items()},
        recall_by_type={
            t: {str(k): statistics.fmean(v) if v else 0.0 for k, v in by_k.items()}
            for t, by_k in sorted(per_type.items())
        },
        throughput_chunks=len(sample),
        throughput_seconds=round(seconds, 3),
        chunks_per_second=round(len(sample) / seconds, 2) if seconds else 0.0,
        peak_rss_mb=peak_rss_mb(),
        load_seconds=round(load_seconds, 2),
    )


def render_comparison(comparison: EmbeddingComparison) -> str:
    ks = sorted({k for r in comparison.results for k in r.recall_at_k}, key=int)
    head = " | ".join(f"recall@{k}" for k in ks)
    lines = [
        f"# Embedding models on {comparison.split} (vector search only)",
        "",
        f"Snapshots: {', '.join(f'{r} {s}' for r, s in sorted(comparison.snapshots.items()))}. "
        f"Machine: {comparison.machine}, {comparison.cpu_count} CPUs.",
        "",
        f"| Model | Dim | {head} | Chunks/s | Peak RSS (MB) | Load (s) |",
        "|---|---|" + "---|" * len(ks) + "---|---|---|",
    ]
    for r in comparison.results:
        recalls = " | ".join(f"{r.recall_at_k[k]:.3f}" for k in ks)
        rss = "-" if r.peak_rss_mb is None else f"{r.peak_rss_mb:.0f}"
        lines.append(
            f"| `{r.model}` | {r.dim} | {recalls} | {r.chunks_per_second:.1f} | {rss} | "
            f"{r.load_seconds:.1f} |"
        )
    lines += ["", "Recall by question type:", ""]
    for r in comparison.results:
        parts = "; ".join(
            f"{t} " + ", ".join(f"@{k} {v:.3f}" for k, v in by_k.items())
            for t, by_k in r.recall_by_type.items()
        )
        lines.append(f"- `{r.model}`: {parts} ({r.queries} queries, {r.chunks} chunks)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# One model per process
# ---------------------------------------------------------------------------


def run_model(
    model: str, split: str, snapshots: dict[str, int], threads: int | None
) -> ModelResult:
    from repoatlas.config import get_settings
    from repoatlas.db.engine import get_engine
    from repoatlas.evals.suites import load_benchmark
    from repoatlas.retrieval.embed import embedder_for

    settings = get_settings().model_copy(
        update={"embedding_model": model, "embedding_threads": threads}
    )
    engine = get_engine()
    items = load_benchmark(split)
    chunks = {repo: load_chunk_rows(engine, sid) for repo, sid in snapshots.items()}
    stored: dict[str, dict[str, np.ndarray]] = {}
    for repo, sid in snapshots.items():
        vectors = stored_vectors(engine, sid, model)
        if vectors is not None:
            stored[repo] = vectors
    return evaluate_model(embedder_for(settings), items, chunks, stored=stored, threads=threads)


def compare_models(
    models: Sequence[str], split: str, snapshots: dict[str, int], *, threads: int | None = None
) -> EmbeddingComparison:
    results = []
    for model in models:
        command = [
            sys.executable,
            "-m",
            "repoatlas.evals.embeddings",
            model,
            split,
            json.dumps(snapshots),
            json.dumps(threads),
        ]
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(f"{model} failed:\n{done.stderr[-2000:]}")
        results.append(ModelResult.model_validate_json(done.stdout.strip().splitlines()[-1]))
    return EmbeddingComparison(
        split=split,
        snapshots=snapshots,
        machine=f"{platform.system()} {platform.machine()} {platform.processor()}".strip(),
        cpu_count=os.cpu_count(),
        results=results,
    )


def _main(argv: list[str]) -> None:
    model, split, snapshots, threads = argv
    result = run_model(model, split, json.loads(snapshots), json.loads(threads))
    sys.stdout.write(result.model_dump_json() + "\n")


def write_comparison(comparison: EmbeddingComparison, directory: Path, date: str) -> Path:
    from repoatlas.canonical import pretty_json, write_text_lf

    path = directory / f"{date}-embeddings-{comparison.split}.json"
    write_text_lf(path, pretty_json(comparison))
    write_text_lf(path.with_suffix(".md"), render_comparison(comparison))
    return path


if __name__ == "__main__":
    _main(sys.argv[1:])
