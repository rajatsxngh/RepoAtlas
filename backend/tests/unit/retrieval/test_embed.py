"""Embedders and the embed stage's place in the fingerprint (RET-3)."""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from repoatlas.config import Settings
from repoatlas.pipeline.stages import config_fingerprint, fingerprint_parts, registered_stages
from repoatlas.retrieval import embed as embed_module
from repoatlas.retrieval.embed import (
    BATCH_TOKEN_BUDGET,
    DEFAULT_MODEL,
    MAX_EMBED_TOKENS,
    Embedder,
    FastEmbedEmbedder,
    HashEmbedder,
    embedder_for,
    estimated_model_tokens,
    hash_words,
    token_batches,
    vector_literal,
)


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("getUserById", ["get", "user", "by", "id"]),
        ("snake_case_name", ["snake", "case", "name"]),
        ("HTTPServer.listen(8080)", ["http", "server", "listen", "8080"]),
    ],
)
def test_hash_words_split_identifiers(text: str, words: list[str]) -> None:
    assert hash_words(text) == words


def test_the_hash_embedder_is_deterministic_and_normalized() -> None:
    embedder = HashEmbedder(64)
    assert isinstance(embedder, Embedder)
    assert (embedder.model_name, embedder.dim) == ("hash-64", 64)
    [first] = embedder.embed_documents(["def add_item(cart, item): cart.items.append(item)"])
    again = HashEmbedder(64).embed_query("def add_item(cart, item): cart.items.append(item)")
    assert first == again and len(first) == 64
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)
    assert embedder.embed_query("") == [0.0] * 64


def test_texts_sharing_words_are_closer() -> None:
    embedder = HashEmbedder(256)
    query = embedder.embed_query("add an item to the cart")
    near, far = embedder.embed_documents(
        ["def add_item(cart): cart.items.append(item)", "def format_price(value): return str"]
    )
    assert cosine(query, near) > cosine(query, far)
    with pytest.raises(ValueError):
        HashEmbedder(4)


def test_embedder_for_reads_the_model_setting(tmp_path: Path) -> None:
    hashed = embedder_for(settings(embedding_model="hash-32"))
    assert isinstance(hashed, HashEmbedder) and hashed.dim == 32
    real = embedder_for(
        settings(embedding_model=DEFAULT_MODEL, data_dir=tmp_path, embedding_threads=2)
    )
    assert isinstance(real, FastEmbedEmbedder)
    assert real.model_name == DEFAULT_MODEL
    assert real.dim == 768  # from fastembed's model list: nothing is downloaded
    assert real._cache_dir == tmp_path / "models"
    assert real._threads == 2
    with pytest.raises(ValueError, match="no model"):
        _ = FastEmbedEmbedder("acme/not-a-model").dim


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def passage_embed(self, texts: list[str], batch_size: int) -> Iterable[np.ndarray]:
        self.calls.append(("passage", (list(texts), batch_size)))
        return (np.array([3.0, 4.0]) for _ in texts)

    def query_embed(self, text: str) -> Iterable[np.ndarray]:
        self.calls.append(("query", text))
        return iter([np.array([0.0, 2.0])])


def test_fastembed_uses_the_passage_and_query_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = FastEmbedEmbedder(DEFAULT_MODEL, batch_size=16)
    fake = FakeModel()
    monkeypatch.setattr(embedder, "_load", lambda: fake)
    assert embedder.embed_documents(["a", "b"]) == [[0.6, 0.8], [0.6, 0.8]]
    assert embedder.embed_query("q") == [0.0, 1.0]
    assert embedder.embed_documents([]) == []
    assert fake.calls == [("passage", (["a", "b"], 2)), ("query", "q")]
    fake.calls.clear()
    long = "x" * 3 * MAX_EMBED_TOKENS  # at the truncation limit
    texts = [long] * 5 + ["short"]
    assert len(embedder.embed_documents(texts)) == 6
    # longest first, at most BATCH_TOKEN_BUDGET / MAX_EMBED_TOKENS long texts per batch
    assert [len(call[1][0]) for call in fake.calls] == [4, 2]


def test_token_batches_group_similar_lengths_within_the_budget() -> None:
    texts = ["a" * 30, "b" * 3_000, "c" * 300, "d" * 30_000, "e" * 3]
    groups = token_batches(texts)
    assert groups[0][0] == 3  # longest first (estimated at the truncation limit)
    assert sorted(i for group in groups for i in group) == list(range(len(texts)))
    for group in groups:
        lengths = [len(texts[i]) for i in group]
        assert lengths == sorted(lengths, reverse=True)
        assert len(group) * estimated_model_tokens(texts[group[0]]) <= BATCH_TOKEN_BUDGET
    assert token_batches(["x"] * 10, max_items=4) == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    assert token_batches([]) == []


def test_the_embed_stage_follows_chunk_and_precedes_publish() -> None:
    names = [stage.name for stage in registered_stages()]
    assert names[names.index("store") :] == ["store", "chunk", "embed", "publish"]
    stages = fingerprint_parts(settings(embedding_model="hash-256"))["stages"]
    assert isinstance(stages, list)
    [embed] = [s for s in stages if isinstance(s, dict) and s["name"] == "embed"]
    assert embed["settings"] == {"embedding_model": "hash-256"}
    assert embed["components"] == {"embedder": embed_module.EMBED_VERSION}


def test_changing_the_model_changes_the_fingerprint() -> None:
    base = config_fingerprint(settings(embedding_model=DEFAULT_MODEL))
    assert config_fingerprint(settings(embedding_model="hash-256")) != base
    assert config_fingerprint(settings(embedding_model="hash-128")) != base
    assert config_fingerprint(settings(embedding_model=DEFAULT_MODEL, embedding_threads=8)) == base


def test_vector_literals_are_pgvector_text() -> None:
    assert vector_literal([1, 0.5, -2.25]) == "[1.0,0.5,-2.25]"
