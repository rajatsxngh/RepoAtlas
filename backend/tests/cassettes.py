"""Recorded LLM responses for tests (ANS-13): replay by default, record on request.

``use_cassette(path)`` replays the cassette at ``path``. With ``REPOATLAS_RECORD_CASSETTES=1``
and an API key it records instead: requests go to the real API through LiteLLM and the
cassette is rewritten when the test saves it. Only tests marked ``live`` record, so
``uv run poe record-cassettes`` (``pytest -m live tests/live``) re-records every cassette.

With ``REPOATLAS_REBASE_CASSETTES=1`` a replay test *rebases* its cassette instead of checking
it: each recorded response is returned in order, and the request the code sends now replaces
the recorded one. No API is called. It is for a prompt change that can't be re-recorded yet
(decision 0021): the cassette keeps its real responses and gains a ``note`` saying that they
were recorded against earlier requests. Review the diff, and re-record when spending is
allowed.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from repoatlas.config import get_settings
from repoatlas.llm.cassette import CassetteTransport
from repoatlas.llm.client import LLMClient
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.llm.transport import LiteLLMTransport

CASSETTES = Path(__file__).parent / "llm_cassettes"
RECORD_ENV = "REPOATLAS_RECORD_CASSETTES"
REBASE_ENV = "REPOATLAS_REBASE_CASSETTES"


def recording() -> bool:
    return os.environ.get(RECORD_ENV) == "1"


def rebasing() -> bool:
    return os.environ.get(REBASE_ENV) == "1" and not recording()


class RebaseTransport(CassetteTransport):
    """Replays the recorded responses in order and keeps the requests sent now."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, mode="replay")
        self.changed = 0

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        if self._next >= len(self.cassette.interactions):
            raise AssertionError(f"{self.path.name} has no interaction {self._next + 1}.")
        interaction = self.cassette.interactions[self._next]
        self._next += 1
        if interaction.error is not None or interaction.response is None:
            raise AssertionError("Only interactions with a recorded response can be rebased.")
        if interaction.request != request:
            interaction.request = json.loads(json.dumps(request))
            self.changed += 1
        return interaction.response

    def save_rebased(self) -> None:
        if not self.changed:
            return
        recorded = self.cassette.recorded_at or "an earlier date"
        today = datetime.now(UTC).date().isoformat()
        self.cassette.note = (
            f"Requests rebased on {today} without calling the API: the responses were recorded "
            f"on {recorded} against the earlier requests (decision 0021). Re-record with "
            "`uv run poe record-cassettes` when spending is allowed, then remove this note."
        )
        data = self.cassette.model_dump(mode="json")
        text = json.dumps(data, indent=2, sort_keys=True) + "\n"
        self.path.write_text(text, encoding="utf-8", newline="\n")  # LF on every OS


def use_cassette(path: Path, description: str = "") -> CassetteTransport:
    """Replay ``path``; record it when recording is on (needs the API key); rebase it when
    rebasing is on."""
    if rebasing():
        return RebaseTransport(path)
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
    if isinstance(transport, RebaseTransport):
        assert transport.exhausted, f"{transport.path.name} has unused interactions."
        transport.save_rebased()
    elif transport.mode == "record":
        transport.cassette.source = "live"
        transport.cassette.note = None
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
