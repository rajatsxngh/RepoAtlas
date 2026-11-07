"""Request and response models of the HTTP API (PRD §8). Graph results reuse the store's own
models (``repoatlas.store.results``), so nodes and edges look the same everywhere."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HealthStatus(_Schema):
    """Liveness: the process answers requests."""

    status: Literal["ok"] = "ok"
    version: str


class ReadyStatus(_Schema):
    """Readiness: the database is reachable and migrated to the revision this code expects."""

    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unreachable", "not_migrated"]
    revision: str | None
    expected_revision: str | None
