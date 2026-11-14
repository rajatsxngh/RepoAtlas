"""What a tool call gives back: a ``ToolResult``, or a ``ToolError``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from repoatlas.retrieval.evidence import EvidenceRef


class ToolResult(BaseModel):
    """What a tool returns. ``evidence`` lists the code ``content`` is about, in the order the
    text mentions it; ``truncated`` says the tool left something out (the text says what)."""

    model_config = ConfigDict(frozen=True)

    content: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    truncated: bool = False


class ToolError(Exception):
    """The call did not run, or failed. ``allowed`` is False when the call was refused because
    it reached outside what tools may touch: an unknown tool, or a path that is not a file of
    the snapshot."""

    def __init__(self, message: str, *, allowed: bool = True) -> None:
        super().__init__(message)
        self.allowed = allowed
