"""The code graph (GRA-2, GRA-6, GRA-10): nodes, edges, unresolved calls, owners.

Positions reuse the parse ``Range``: 1-based lines and 0-based UTF-8 byte columns into the blob.
A graph keeps its collections in canonical order from the moment it is built (nodes by key;
edges by src, dst, kind, site; unresolved calls by caller and site), so ``to_json()`` and
``digest()`` depend only on content, never on the order things were found in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from repoatlas.canonical import canonical_json, digest
from repoatlas.parsing.models import Range

CONFIDENCE_DIGITS = 4  # confidences and shares are rounded so their JSON stays short and stable


class NodeKind(StrEnum):
    DIR = "dir"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    ROUTE = "route"
    TEST = "test"


class EdgeKind(StrEnum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    TESTED_BY = "tested_by"  # production → test
    ROUTES_TO = "routes_to"  # route → handler


class Provenance(StrEnum):
    TREE_SITTER = "tree_sitter"  # structure seen directly
    IMPORT_RESOLVER = "import_resolver"
    NAME_MATCH = "name_match"  # call resolution tiers
    CONVENTION = "convention"  # naming conventions
    GIT = "git"


# ambiguous: more candidates than the cap · not_found: ``obj.m()`` with no ``m`` in the repo ·
# unbound_name: a bare name that is neither defined, imported, nor a builtin ·
# dynamic: the callee is not a name (``f()()``, ``handlers[k]()``)
UnresolvedReason = Literal["ambiguous", "not_found", "unbound_name", "dynamic"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Node(_Model):
    """A directory, file, symbol, or route. ``dir`` and ``file`` nodes have no ranges."""

    key: str
    kind: NodeKind
    name: str
    qualified_name: str
    path: str
    name_range: Range | None = None
    span: Range | None = None
    parent_key: str | None = None
    exported: bool = False
    meta: dict[str, JsonValue] = {}


class Edge(_Model):
    """A directed edge. ``site`` lies in the source node's file, except for ``tested_by``
    edges, whose evidence (an import or a call) lies in the test (destination) file."""

    src: str
    dst: str
    kind: EdgeKind
    provenance: Provenance
    confidence: float = Field(gt=0, le=1)
    site: Range | None = None
    meta: dict[str, JsonValue] = {}

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, value: float) -> float:
        rounded = round(value, CONFIDENCE_DIGITS)
        if rounded <= 0:
            raise ValueError("confidence must stay above 0 after rounding")
        return rounded


class UnresolvedCall(_Model):
    """A call site that produced no edge; kept as data for impact analysis (GRA-10)."""

    caller_key: str
    callee_text: str
    receiver_text: str | None
    site: Range
    candidates: list[str] = []
    reason: UnresolvedReason


class Owner(_Model):
    """One author's share of a file's commits. The email is only ever stored hashed."""

    author_name: str
    author_hash: str
    commits: int = Field(ge=1)
    last_commit_at: datetime
    share: float = Field(gt=0, le=1)

    @field_validator("last_commit_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("last_commit_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("share")
    @classmethod
    def _round_share(cls, value: float) -> float:
        return round(value, CONFIDENCE_DIGITS)


class GraphStats(_Model):
    """Counts for ``repoatlas graph --stats``. Every call site lands in exactly one bucket:
    resolved (by the tier that produced its edges), unresolved, external, or builtin."""

    nodes_by_kind: dict[str, int] = {}
    edges_by_kind: dict[str, int] = {}
    call_sites: int = 0
    call_sites_by_tier: dict[str, int] = {}
    unresolved_calls: int = 0
    unresolved_by_reason: dict[str, int] = {}
    external_calls: int = 0
    builtin_calls: int = 0
    import_statements: int = 0
    imports_by_outcome: dict[str, int] = {}  # internal, external, missing
    history_commits: int = 0
    files_with_owners: int = 0


def _site_key(site: Range | None) -> tuple[int, int, int, int]:
    if site is None:
        return (-1, -1, -1, -1)
    return (site.start_line, site.start_col, site.end_line, site.end_col)


def edge_sort_key(edge: Edge) -> tuple[str, str, str, tuple[int, int, int, int], str, float, str]:
    return (
        edge.src,
        edge.dst,
        edge.kind.value,
        _site_key(edge.site),
        edge.provenance.value,
        edge.confidence,
        canonical_json(edge.meta),
    )


def unresolved_sort_key(call: UnresolvedCall) -> tuple[str, tuple[int, int, int, int], str, str]:
    return (call.caller_key, _site_key(call.site), call.callee_text, call.reason)


def owner_sort_key(owner: Owner) -> tuple[int, float, str, str]:
    return (-owner.commits, -owner.last_commit_at.timestamp(), owner.author_hash, owner.author_name)


class CodeGraph(_Model):
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    unresolved: list[UnresolvedCall] = []
    owners: dict[str, list[Owner]] = {}  # file path → owners, most commits first
    stats: GraphStats = GraphStats()

    @field_validator("nodes")
    @classmethod
    def _sort_nodes(cls, value: dict[str, Node]) -> dict[str, Node]:
        for key, node in value.items():
            if key != node.key:
                raise ValueError(f"node {node.key!r} is stored under {key!r}")
        return dict(sorted(value.items()))

    @field_validator("edges")
    @classmethod
    def _sort_edges(cls, value: list[Edge]) -> list[Edge]:
        return sorted(value, key=edge_sort_key)

    @field_validator("unresolved")
    @classmethod
    def _sort_unresolved(cls, value: list[UnresolvedCall]) -> list[UnresolvedCall]:
        return sorted(value, key=unresolved_sort_key)

    @field_validator("owners")
    @classmethod
    def _sort_owners(cls, value: dict[str, list[Owner]]) -> dict[str, list[Owner]]:
        return {path: sorted(value[path], key=owner_sort_key) for path in sorted(value)}

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, no whitespace, literal Unicode."""
        return canonical_json(self)

    @classmethod
    def from_json(cls, text: str | bytes) -> CodeGraph:
        return cls.model_validate_json(text)

    def digest(self) -> str:
        """sha256 of the canonical JSON."""
        return digest(self)

    def edges_of(self, kind: EdgeKind) -> list[Edge]:
        return [edge for edge in self.edges if edge.kind == kind]
