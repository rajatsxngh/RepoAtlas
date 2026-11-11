"""Recorded responses ("cassettes") for LLM tests (ANS-13).

A cassette is a JSON file of interactions, each a request body and the response body (or the
HTTP error) it got. ``CassetteTransport`` replays one in order and fails when a request differs
from the recorded one, so a change to prompts or request rules shows up as a test failure until
the cassette is re-recorded. In record mode it forwards to a real transport and writes what it
sees. Only bodies are stored: transports never handle headers, so cassettes hold no secrets.

``source`` says where the responses came from: ``live`` (recorded from the API) or ``docs``
(written by hand from the documented shape, to be replaced by a live recording).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from repoatlas.canonical import canonical_json
from repoatlas.llm.transport import Transport, error_response, scrub

CassetteMode = Literal["replay", "record"]


class Interaction(BaseModel):
    request: dict[str, Any]
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None  # {"status": int, "message": str}


class Cassette(BaseModel):
    description: str = ""
    source: Literal["live", "docs"] = "live"
    recorded_at: str | None = None
    interactions: list[Interaction] = Field(default_factory=list)


class CassetteMismatch(AssertionError):
    """A request differs from the recorded one (re-record the cassette if the change is
    intended)."""


class CassetteTransport:
    def __init__(
        self,
        path: Path,
        mode: CassetteMode = "replay",
        inner: Transport | None = None,
        description: str = "",
    ) -> None:
        self.path = path
        self.mode = mode
        self.inner = inner
        self.requests: list[dict[str, Any]] = []
        if mode == "replay":
            self.cassette = Cassette.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            if inner is None:
                raise ValueError("Recording needs a real transport.")
            self.cassette = Cassette(
                description=description, recorded_at=datetime.now(UTC).date().isoformat()
            )
        self._next = 0

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        if self.mode == "record":
            return await self._record(request, timeout_s)
        if self._next >= len(self.cassette.interactions):
            raise CassetteMismatch(f"{self.path.name} has no interaction {self._next + 1}.")
        interaction = self.cassette.interactions[self._next]
        self._next += 1
        if canonical_json(request) != canonical_json(interaction.request):
            raise CassetteMismatch(
                f"Request {self._next} differs from {self.path.name}:\n"
                f"recorded: {canonical_json(interaction.request)}\n"
                f"sent:     {canonical_json(request)}"
            )
        if interaction.error is not None:
            raise error_response(int(interaction.error["status"]), interaction.error["message"])
        assert interaction.response is not None
        return interaction.response

    async def _record(self, request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        assert self.inner is not None
        try:
            response = await self.inner.send(request, timeout_s=timeout_s)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if not isinstance(status, int):
                raise
            self.cassette.interactions.append(
                Interaction(request=request, error={"status": status, "message": scrub(str(exc))})
            )
            raise
        self.cassette.interactions.append(Interaction(request=request, response=response))
        return response

    @property
    def exhausted(self) -> bool:
        return self._next == len(self.cassette.interactions)

    def save(self) -> None:
        """Write the recorded cassette (record mode)."""
        if self.mode != "record":
            raise ValueError("Only a recording cassette can be saved.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self.cassette.model_dump(mode="json")
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
