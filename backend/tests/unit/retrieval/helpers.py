"""Fixture graphs and their chunks, for retrieval unit tests."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from repoatlas.graph.model import CodeGraph
from repoatlas.retrieval.chunker import Chunk, chunk_graph
from repoatlas.store.blobs import decode_blob

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@cache
def fixture_graph(name: str) -> CodeGraph:
    data = json.loads((FIXTURES / "golden" / f"{name}.graph.json").read_bytes())
    return CodeGraph.model_validate(data["graph"])


def fixture_text(name: str, path: str) -> str | None:
    file = FIXTURES / "repos" / name / path
    return decode_blob(file.read_bytes()).text if file.is_file() else None


@cache
def fixture_chunks(name: str) -> tuple[Chunk, ...]:
    return tuple(chunk_graph(fixture_graph(name), lambda path: fixture_text(name, path)))


def by_key(name: str) -> dict[str, list[Chunk]]:
    found: dict[str, list[Chunk]] = {}
    for chunk in fixture_chunks(name):
        found.setdefault(chunk.node_key, []).append(chunk)
    return found
