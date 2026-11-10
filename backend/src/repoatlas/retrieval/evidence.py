"""Evidence references: the one format for pointing at code (PRD §7.4, RET-2).

An ``EvidenceRef`` names exact lines (1-based, inclusive) of one git blob at one path, with the
symbol they belong to when there is one. Retrieval chunks, citations, agent tools, and impact
evidence all use it. Its compact string form is ``path#L10-L24@abc1234`` (the first 7 hex digits
of the blob OID). A reference read back from compact form carries only that abbreviated OID;
pass the full OID to ``from_compact`` to check it and keep it.
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHORT_OID = 7
_OID = re.compile(r"[0-9a-f]{7,40}")
_COMPACT = re.compile(r"(?P<path>.+)#L(?P<start>\d+)(?:-L(?P<end>\d+))?@(?P<oid>[0-9a-f]{7,40})")


class EvidenceRef(BaseModel):
    """Lines ``start_line`` to ``end_line`` of blob ``blob_oid`` at ``path``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blob_oid: str = Field(pattern=_OID.pattern)
    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    symbol_key: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError(f"end_line {self.end_line} is before start_line {self.start_line}.")
        return self

    @property
    def abbreviated(self) -> bool:
        """The OID is a prefix (as read from compact form), not the full 40 hex digits."""
        return len(self.blob_oid) < 40

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def to_compact(self) -> str:
        """``path#L<start>-L<end>@<first 7 hex of the OID>``."""
        oid = self.blob_oid[:SHORT_OID]
        return f"{self.path}#L{self.start_line}-L{self.end_line}@{oid}"

    @classmethod
    def from_compact(
        cls, value: str, *, blob_oid: str | None = None, symbol_key: str | None = None
    ) -> EvidenceRef:
        """Parse the compact form (``#L10`` alone means one line). With ``blob_oid``, the full
        OID must start with the compact one and replaces it."""
        match = _COMPACT.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"{value!r} is not an evidence reference (path#L1-L2@abc1234).")
        start = int(match["start"])
        end = int(match["end"]) if match["end"] is not None else start
        oid = match["oid"]
        if blob_oid is not None:
            if not blob_oid.startswith(oid):
                raise ValueError(f"Blob {blob_oid} does not match {oid}.")
            oid = blob_oid
        return cls(
            blob_oid=oid, path=match["path"], start_line=start, end_line=end, symbol_key=symbol_key
        )

    def same_blob(self, other: EvidenceRef) -> bool:
        """Both point into the same blob at the same path (abbreviated OIDs match by prefix)."""
        short = min(len(self.blob_oid), len(other.blob_oid))
        return self.path == other.path and self.blob_oid[:short] == other.blob_oid[:short]

    def contains(self, other: EvidenceRef) -> bool:
        """``other``'s lines all lie within these lines of the same blob."""
        return (
            self.same_blob(other)
            and self.start_line <= other.start_line
            and other.end_line <= self.end_line
        )

    def overlaps(self, other: EvidenceRef) -> bool:
        return (
            self.same_blob(other)
            and self.start_line <= other.end_line
            and other.start_line <= self.end_line
        )

    def __str__(self) -> str:
        return self.to_compact()
