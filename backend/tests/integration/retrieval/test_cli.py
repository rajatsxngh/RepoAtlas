"""``repoatlas search``: ranked evidence and ``--explain`` (RET-8)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from repoatlas.cli import app
from repoatlas.config import get_settings
from repoatlas.retrieval.embed import HashEmbedder, embed_chunks
from tests.integration.retrieval.conftest import Stored

ROUND = "py:src/shop/utils/helpers.py:round_money:function"


@pytest.fixture
def cli_env(
    stored: Stored, pg_engine: Engine, pg_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Stored]:
    for key in list(os.environ):
        if key.startswith("REPOATLAS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", pg_url)
    monkeypatch.setenv("REPOATLAS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REPOATLAS_EMBEDDING_MODEL", "hash-256")
    embed_chunks(stored.py, HashEmbedder(256), engine=pg_engine)
    get_settings.cache_clear()
    yield stored
    get_settings.cache_clear()


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["search", *args])
    return result.exit_code, result.output


def test_search_prints_ranked_evidence(cli_env: Stored) -> None:
    code, output = run("--snapshot", str(cli_env.py), "how is money rounded? round_money")
    assert code == 0, output
    lines = output.splitlines()
    assert lines[0].startswith(f"Snapshot {cli_env.py} | hybrid | model hash-256 | ")
    assert lines[1].startswith("  1. ") and ROUND in lines[1]
    assert "src/shop/utils/helpers.py#L4-L5@" in lines[1]
    assert "[graph, keyword, vector]" in lines[1] or "[keyword, vector]" in lines[1]


def test_explain_prints_every_step(cli_env: Stored) -> None:
    code, output = run("-s", str(cli_env.py), "Cart.add", "--explain", "--code")
    assert code == 0, output
    for title in (
        "Keyword query: cart or add",
        "Vector search (cosine similarity)",
        "Keyword search (ts_rank_cd)",
        "Fused (reciprocal rank fusion, 1 / (60 + rank))",
        "Seeds (",
        "Graph expansion (seed x confidence x 0.5^hops)",
        "Ranked (max(fused, expanded) + name bonus)",
        "Timings: vector ",
    ):
        assert title in output, title
    assert "self.items.append((name, round_money(price)))" in output  # --code
    assert "from py:src/shop/cart.py:Cart.add:method" in output  # expansion names its seed


def test_single_modes_and_json(cli_env: Stored) -> None:
    code, output = run("-s", str(cli_env.py), "round_money", "--mode", "keyword", "--explain")
    assert code == 0 and "Vector search" not in output and "Fused" not in output
    code, output = run("-s", str(cli_env.py), "round_money", "--json", "-k", "2")
    assert code == 0
    data = json.loads(output)
    assert len(data["evidence"]) == 2
    assert data["evidence"][0]["node_key"] == ROUND
    assert data["debug"]["mode"] == "hybrid" and data["debug"]["k"] == 2


def test_bad_input_fails_with_a_message(cli_env: Stored) -> None:
    code, output = run("-s", str(cli_env.py), "cart", "--mode", "fuzzy")
    assert code == 2 and "--mode must be one of vector, keyword, hybrid" in output
    code, output = run("-s", "999999", "cart")
    assert code == 1 and "does not exist" in output
    code, output = run("-s", str(cli_env.ts), "cart")  # ts_small has no embeddings here
    assert code == 0 and "Note: The snapshot has no embeddings" in output
    code, output = run("-s", str(cli_env.ts), "cart", "--mode", "vector")
    assert code == 1 and "no embeddings" in output
