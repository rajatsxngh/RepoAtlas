"""Impact contracts (IMP-3, IMP-6): parameters, targets, and the report.

A report lists the affected symbols once, in one combined ranking (score, then best single-path
score, then depth, then key); each item carries its group, its best path from a changed symbol,
and the evidence for the last step of that path. Unknown sites (call sites the graph could not
resolve, and string literals naming the target) are listed apart and never count as safe.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from repoatlas.graph.model import EdgeKind, NodeKind
from repoatlas.impact.traversal import TRAVERSABLE_KINDS
from repoatlas.retrieval.evidence import EvidenceRef

ImpactGroupName = Literal["test", "public_api", "caller"]
GROUPS: tuple[ImpactGroupName, ...] = ("test", "public_api", "caller")
PathStepKind = Literal[
    "calls", "inherits", "imports", "tested_by", "routes_to", "overrides", "contains"
]
UnknownReason = Literal["ambiguous", "not_found", "unbound_name", "dynamic", "string_literal"]
MAX_DIFF_CHARS = 60_000


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ImpactParams(_Model):
    """Search settings. Defaults are the values chosen on the dev split (decision 0019)."""

    max_depth: int = Field(default=4, ge=0, le=10)
    decay: float = Field(default=0.6, gt=0, le=1)
    min_score: float = Field(default=0.05, ge=0, le=1)
    limit: int = Field(default=200, ge=1, le=1000)
    edge_kinds: tuple[EdgeKind, ...] = Field(default=TRAVERSABLE_KINDS, min_length=1)


class SymbolTarget(_Model):
    kind: Literal["symbol"] = "symbol"
    symbol_key: str = Field(min_length=1, max_length=2_000)


class DiffTarget(_Model):
    kind: Literal["diff"] = "diff"
    diff: str = Field(min_length=1, max_length=MAX_DIFF_CHARS)


Target = Annotated[SymbolTarget | DiffTarget, Field(discriminator="kind")]


class PathStep(_Model):
    """One step from a changed symbol towards an affected one (``src`` is nearer the change)."""

    src: str
    dst: str
    kind: PathStepKind
    confidence: float


class ImpactItem(_Model):
    symbol_key: str
    kind: NodeKind
    name: str
    path: str
    group: ImpactGroupName
    score: float = Field(ge=0, le=1)  # noisy-or over paths
    best_score: float = Field(ge=0, le=1)  # the best single path's score
    depth: int = Field(ge=1)
    best_path: list[PathStep]
    evidence: list[EvidenceRef]


class UnknownSite(_Model):
    """A place that may depend on the target but that the graph cannot confirm."""

    site: EvidenceRef  # its ``symbol_key`` is the enclosing symbol (or the file)
    callee_text: str
    candidates: list[str] = []
    reason: UnknownReason


class ImpactStats(_Model):
    targets: int
    reached: int  # nodes the search reached, the changed symbols excluded
    expanded: int
    items: int  # items at or above ``min_score``
    returned: int
    truncated: bool  # more items than ``limit``
    by_group: dict[str, int]
    unknown: int
    elapsed_ms: float


class ImpactReport(_Model):
    target: Target
    targets: list[str]  # the changed symbols the search started from
    params: ImpactParams
    items: list[ImpactItem]  # combined ranking
    unknown: list[UnknownSite]
    stats: ImpactStats

    def group(self, name: ImpactGroupName) -> list[ImpactItem]:
        """One group's items, ranked."""
        return [item for item in self.items if item.group == name]
