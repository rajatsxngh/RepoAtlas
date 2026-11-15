"""The retrieval stages of a build, registered between ``store`` and ``publish``.

``chunk`` cuts the stored snapshot into chunks (read from ``blobs``) and saves them with their
keyword index; in an incremental build it copies the chunks of unchanged files from the
previous snapshot instead of cutting them again. ``embed`` gives every chunk a vector for the
configured model, through the shared ``embeddings`` cache; the model name is one of its
settings, so changing the model changes the config fingerprint and snapshots never mix models.
Each stage is idempotent (it replaces or completes its own rows), so a retried build gives the
same result.
"""

from __future__ import annotations

from pydantic import JsonValue

import repoatlas.pipeline.build  # noqa: F401  (registers store, which chunk follows)
from repoatlas.pipeline.context import BuildContext
from repoatlas.pipeline.stages import Stage, register_builtin
from repoatlas.retrieval import chunker
from repoatlas.retrieval.chunkstore import save_chunks
from repoatlas.retrieval.embed import EMBED_VERSION, embed_chunks, embedder_for
from repoatlas.retrieval.keyword import KEYWORD_VERSION
from repoatlas.retrieval.reuse import (
    ChunkReuse,
    chunk_stats,
    plan_chunk_reuse,
    save_chunks_reusing,
)


def chunk(ctx: BuildContext) -> None:
    """Cut the snapshot into chunks. With a previous snapshot of the repository (an incremental
    build, decision 0024), the chunks of files that did not change are copied from it."""
    snapshot_id = ctx.need(ctx.snapshot_id, "snapshot")
    previous = ctx.previous_snapshot_id
    reuse = ChunkReuse()
    if previous is not None:
        reuse = plan_chunk_reuse(snapshot_id, previous, engine=ctx.engine)
    chunks = chunker.chunk_snapshot(snapshot_id, engine=ctx.engine, skip_paths=reuse.paths)
    ctx.progress(0.5)
    copied = 0
    if previous is not None and reuse.paths:
        copied = save_chunks_reusing(snapshot_id, previous, reuse, chunks, engine=ctx.engine)
    else:
        save_chunks(snapshot_id, chunks, engine=ctx.engine)
    stats: dict[str, JsonValue] = chunk_stats(snapshot_id, engine=ctx.engine)
    ctx.stats["chunks"] = {
        **stats,
        "reused": copied,
        "rebuilt": len(chunks),
        "files_reused": len(reuse.paths),
    }


def _chunk_components() -> dict[str, JsonValue]:
    return {
        "chunker": chunker.CHUNKER_VERSION,
        "max_tokens": chunker.MAX_CHUNK_TOKENS,
        "overlap_lines": chunker.OVERLAP_LINES,
        "keyword": KEYWORD_VERSION,
    }


def embed(ctx: BuildContext) -> None:
    stats = embed_chunks(
        ctx.need(ctx.snapshot_id, "snapshot"),
        embedder_for(ctx.settings),
        engine=ctx.engine,
        progress=lambda done, total: ctx.progress(done / total),
    )
    ctx.stats["embeddings"] = stats.as_json()


def _embed_components() -> dict[str, JsonValue]:
    return {"embedder": EMBED_VERSION}


CHUNK_STAGE = Stage("chunk", 1, (), chunk, weight=0.5, components=_chunk_components)
EMBED_STAGE = Stage(
    "embed", 1, ("embedding_model",), embed, weight=3.0, components=_embed_components
)
RETRIEVAL_STAGES: tuple[Stage, ...] = (CHUNK_STAGE, EMBED_STAGE)

register_builtin(CHUNK_STAGE, after="store")
register_builtin(EMBED_STAGE, after="chunk")
