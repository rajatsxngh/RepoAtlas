"""Embeddings: the ``Embedder`` protocol, a local fastembed model, and a hashing stand-in
(RET-3, decision 0009).

``FastEmbedEmbedder`` runs a fastembed model on CPU; the model is downloaded on first use into
``data_dir/models`` (a one-time download of about 0.6 GB for the default). Documents and
queries go through fastembed's passage and query paths, so models with query/document
conventions get them. Texts are truncated at ``MAX_EMBED_TOKENS`` and embedded in groups of
similar length within ``BATCH_TOKEN_BUDGET``, so memory stays bounded on CPU.
``HashEmbedder`` hashes words into a fixed number of dimensions: it is deterministic, needs no
download, and is what tests use (``REPOATLAS_EMBEDDING_MODEL=hash-256``).

``embed_chunks`` embeds a snapshot's chunks through the ``embeddings`` cache: only chunk hashes
with no vector for the model yet are embedded (longest first, in batches of ``BATCH_SIZE``, so
each batch holds chunks of similar length), and the snapshot records the model and dimension
it was embedded with.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import Engine, text

from repoatlas.config import Settings
from repoatlas.db.engine import get_engine
from repoatlas.logging import get_logger

if TYPE_CHECKING:
    from fastembed import TextEmbedding

EMBED_VERSION = 2  # 2: documents truncated at MAX_EMBED_TOKENS
BATCH_SIZE = 64
MAX_EMBED_TOKENS = 2_048  # chunks stay near 1,200 estimated tokens; this bounds the outliers
BATCH_TOKEN_BUDGET = 8_192  # texts per model batch x longest estimated tokens in the batch
DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-code"
HASH_PREFIX = "hash-"

log = get_logger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """Turns text into unit vectors of ``dim`` dimensions with one model."""

    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _normalize(values: Iterable[float]) -> list[float]:
    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    return vector if norm == 0 else [value / norm for value in vector]


# --- hashing (tests) ----------------------------------------------------------------------

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def hash_words(value: str) -> list[str]:
    """Lower-cased words of ``value``: identifiers split at case changes, digits, and
    punctuation (``getUserById`` → ``get``, ``user``, ``by``, ``id``)."""
    return [word.lower() for word in _WORD.findall(value)]


class HashEmbedder:
    """A deterministic bag-of-words embedder: each word adds ±1 to one hashed dimension
    (the sign is hashed too), then the vector is normalized. Texts sharing words are close."""

    def __init__(self, dim: int = 256) -> None:
        if dim < 8:
            raise ValueError("A hash embedder needs at least 8 dimensions.")
        self._dim = dim

    @property
    def model_name(self) -> str:
        return f"{HASH_PREFIX}{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _embed(self, value: str) -> list[float]:
        vector = [0.0] * self._dim
        for word in hash_words(value):
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "big")
            vector[number % self._dim] += 1.0 if (number >> 63) & 1 else -1.0
        return _normalize(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(value) for value in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


# --- fastembed ----------------------------------------------------------------------------


class FastEmbedEmbedder:
    """A fastembed text model on CPU, loaded on first use."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: Path | None = None,
        threads: int | None = None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = batch_size
        self._model: TextEmbedding | None = None
        self._dim: int | None = None
        self._lock = threading.Lock()  # concurrent first queries load the model once

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = _fastembed_dim(self._model_name)
        return self._dim

    def _load(self) -> TextEmbedding:
        if self._model is not None:
            return self._model
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> TextEmbedding:
        if self._model is None:
            from fastembed import TextEmbedding

            log.info("loading the embedding model", model=self._model_name)
            model = TextEmbedding(
                self._model_name,
                cache_dir=None if self._cache_dir is None else str(self._cache_dir),
                threads=self._threads,
            )
            tokenizer = getattr(model.model, "tokenizer", None)
            limit = (getattr(tokenizer, "truncation", None) or {}).get("max_length")
            if tokenizer is not None and (limit is None or limit > MAX_EMBED_TOKENS):
                tokenizer.enable_truncation(max_length=MAX_EMBED_TOKENS)
            self._model = model
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` in groups of similar length whose padded size stays within
        ``BATCH_TOKEN_BUDGET`` (attention memory grows with batch x length squared)."""
        if not texts:
            return []
        model = self._load()
        vectors: list[list[float]] = [[] for _ in texts]
        for group in token_batches(texts, max_items=self._batch_size):
            embedded = model.passage_embed([texts[i] for i in group], batch_size=len(group))
            for index, vector in zip(group, embedded, strict=True):
                vectors[index] = _normalize(vector.tolist())
        return vectors

    def embed_query(self, text: str) -> list[float]:
        [vector] = list(self._load().query_embed(text))
        return _normalize(vector.tolist())


def estimated_model_tokens(value: str) -> int:
    """A generous token estimate for code (about 3 characters per token), capped where the
    model truncates."""
    return min(MAX_EMBED_TOKENS, max(1, -(-len(value) // 3)))


def token_batches(texts: Sequence[str], *, max_items: int = BATCH_SIZE) -> list[list[int]]:
    """Indices of ``texts``, longest first, grouped so that each group's size times its
    longest estimate stays within ``BATCH_TOKEN_BUDGET`` (and at most ``max_items``)."""
    order = sorted(range(len(texts)), key=lambda i: (-len(texts[i]), i))
    groups: list[list[int]] = []
    for index in order:
        if groups:
            group = groups[-1]
            longest = estimated_model_tokens(texts[group[0]])
            if len(group) < max_items and (len(group) + 1) * longest <= BATCH_TOKEN_BUDGET:
                group.append(index)
                continue
        groups.append([index])
    return groups


def _fastembed_dim(model_name: str) -> int:
    from fastembed import TextEmbedding

    for description in TextEmbedding.list_supported_models():
        if description["model"] == model_name:
            return int(description["dim"])
    raise ValueError(f"fastembed has no model named {model_name!r}.")


def embedder_for(settings: Settings) -> Embedder:
    """The embedder ``settings.embedding_model`` names: ``hash-<dim>`` or a fastembed model."""
    name = settings.embedding_model
    if name.startswith(HASH_PREFIX):
        return HashEmbedder(int(name.removeprefix(HASH_PREFIX)))
    return FastEmbedEmbedder(
        name, cache_dir=settings.data_dir / "models", threads=settings.embedding_threads
    )


# --- the embeddings cache -----------------------------------------------------------------


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form (``[0.1,0.2]``), cast with ``CAST(:v AS vector)``."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


@dataclass(frozen=True, slots=True)
class EmbedStats:
    model: str
    dim: int
    chunks: int  # distinct chunk hashes of the snapshot
    embedded: int  # newly embedded
    cached: int  # found in the cache

    def as_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "dim": self.dim,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "cached": self.cached,
        }


def embed_chunks(
    snapshot_id: int,
    embedder: Embedder,
    *,
    engine: Engine | None = None,
    batch_size: int = BATCH_SIZE,
    progress: Callable[[int, int], None] | None = None,
) -> EmbedStats:
    """Make sure every chunk of the snapshot has a vector for ``embedder``'s model, embedding
    only the chunk hashes the cache lacks, and record the model on the snapshot."""
    engine = engine or get_engine()
    model = embedder.model_name
    with engine.connect() as connection:
        missing = connection.execute(
            text(
                "SELECT DISTINCT ON (c.chunk_hash) c.chunk_hash, c.header, c.text FROM chunks c "
                "WHERE c.snapshot_id = :s AND NOT EXISTS (SELECT 1 FROM embeddings e "
                "WHERE e.chunk_hash = c.chunk_hash AND e.model = :m) ORDER BY c.chunk_hash"
            ),
            {"s": snapshot_id, "m": model},
        ).all()
        total = connection.execute(
            text("SELECT count(DISTINCT chunk_hash) FROM chunks WHERE snapshot_id = :s"),
            {"s": snapshot_id},
        ).scalar_one()
    # Longest first: batches of similar length waste less padding, and a batch too large for
    # memory fails at the start rather than after most of the work.
    missing = sorted(missing, key=lambda row: (-len(row.header) - len(row.text), row.chunk_hash))
    insert = text(
        "INSERT INTO embeddings (chunk_hash, model, dim, vector) "
        "VALUES (:h, :m, :d, CAST(:v AS vector)) ON CONFLICT DO NOTHING"
    )
    dim = embedder.dim
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset : offset + batch_size]
        vectors = embedder.embed_documents([f"{row.header}\n{row.text}" for row in batch])
        rows = []
        for row, vector in zip(batch, vectors, strict=True):
            if len(vector) != dim:
                raise ValueError(f"{model} gave {len(vector)} dimensions, not {dim}.")
            rows.append({"h": row.chunk_hash, "m": model, "d": dim, "v": vector_literal(vector)})
        with engine.begin() as connection:
            connection.execute(insert, rows)
        if progress is not None:
            progress(offset + len(batch), len(missing))
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE snapshots SET embedding_model = :m, embedding_dim = :d, "
                "updated_at = now() WHERE id = :s"
            ),
            {"m": model, "d": dim, "s": snapshot_id},
        )
    return EmbedStats(
        model=model,
        dim=dim,
        chunks=int(total),
        embedded=len(missing),
        cached=int(total) - len(missing),
    )
