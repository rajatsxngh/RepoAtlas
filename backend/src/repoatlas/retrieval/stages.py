"""The retrieval stages of a build, registered between ``store`` and ``publish``.

``chunk`` cuts the stored snapshot into chunks (read from ``blobs``) and saves them with their
keyword index. Each stage is idempotent: it replaces its own rows, so a retried build gives
the same result.
"""

from __future__ import annotations

from collections import Counter

from pydantic import JsonValue

import repoatlas.pipeline.build  # noqa: F401  (registers store, which chunk follows)
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import Stage, register_builtin
from repoatlas.retrieval import chunker
from repoatlas.retrieval.chunkstore import save_chunks


def chunk(ctx: BuildContext) -> None:
    snapshot_id = ctx.need(ctx.snapshot_id, "snapshot")
    chunks = chunker.chunk_snapshot(snapshot_id, engine=ctx.engine)
    ctx.progress(0.5)
    save_chunks(snapshot_id, chunks, engine=ctx.engine)
    counts = Counter(item.kind for item in chunks)
    by_kind: dict[str, JsonValue] = {kind: counts[kind] for kind in sorted(counts)}
    ctx.stats["chunks"] = {
        "count": len(chunks),
        "by_kind": by_kind,
        "split_parts": sum(1 for item in chunks if item.part),
        "tokens": sum(item.tokens for item in chunks),
    }


def _chunk_components() -> dict[str, JsonValue]:
    return {
        "chunker": chunker.CHUNKER_VERSION,
        "max_tokens": chunker.MAX_CHUNK_TOKENS,
        "overlap_lines": chunker.OVERLAP_LINES,
    }


CHUNK_STAGE = Stage("chunk", 1, (), chunk, weight=0.5, components=_chunk_components)
RETRIEVAL_STAGES: tuple[Stage, ...] = (CHUNK_STAGE,)

register_builtin(CHUNK_STAGE, after="store")
