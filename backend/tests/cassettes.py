"""Recorded LLM responses for tests (ANS-13): replay by default, record on request.

``use_cassette(path)`` replays the cassette at ``path``. With ``REPOATLAS_RECORD_CASSETTES=1``
and an API key it records instead: requests go to the real API through LiteLLM and the
cassette is rewritten when the test saves it. Only tests marked ``live`` record, so
``uv run poe record-cassettes`` (``pytest -m live tests/live``) re-records every cassette.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repoatlas.config import get_settings
from repoatlas.llm.cassette import CassetteTransport
from repoatlas.llm.client import LLMClient
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.transport import LiteLLMTransport

CASSETTES = Path(__file__).parent / "llm_cassettes"
RECORD_ENV = "REPOATLAS_RECORD_CASSETTES"


def recording() -> bool:
    return os.environ.get(RECORD_ENV) == "1"


def use_cassette(path: Path, description: str = "") -> CassetteTransport:
    """Replay ``path``; record it when recording is on (needs the API key)."""
    if not recording():
        return CassetteTransport(path)
    key = get_settings().anthropic_api_key
    if key is None:
        pytest.skip("Recording needs REPOATLAS_ANTHROPIC_API_KEY.")
    return CassetteTransport(
        path, mode="record", inner=LiteLLMTransport(key), description=description
    )


def finish(transport: CassetteTransport) -> None:
    """Save a recording (marked live), or check that a replay used every interaction."""
    if transport.mode == "record":
        transport.cassette.source = "live"
        transport.save()
    else:
        assert transport.exhausted, f"{transport.path.name} has unused interactions."


def cassette_client(transport: CassetteTransport) -> LLMClient:
    """A client with the default models, efforts, and prices over ``transport`` (the request
    bodies depend on these, so cassettes record them as they are by default)."""
    return LLMClient(
        transport,
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        efforts={"small": "low", "large": "medium"},
        prices=PriceTable(DEFAULT_PRICES),
        max_retries=0 if transport.mode == "replay" else 3,
    )
