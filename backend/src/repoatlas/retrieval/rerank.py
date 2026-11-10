"""Score-fusion rerank and token-budget packing (RET-6).

``rerank`` gives each candidate chunk ``max(fused score, expanded score)`` plus ``NAME_BONUS``
when an identifier in the query names the chunk's symbol exactly (case-insensitive): a plain
identifier matches the symbol's name, and a dotted one (``Cart.add``, ``shop.cart.Cart``) its
qualified name, alone or after a module path. ``pack`` takes candidates in score order while
they fit the token budget, skipping a chunk whose lines lie wholly inside an already selected
chunk. Only chunks whose text is their exact lines (functions, methods, tests) can contain
another: class chunks leave member bodies out and module chunks skip symbols, so neither
stands in for the code inside its range.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.keyword import identifiers

NAME_BONUS = 1.0 / 60  # about one first place in a reciprocal-rank list
DEFAULT_BUDGET_TOKENS = 12_000
CONTIGUOUS_KINDS: frozenset[str] = frozenset({"function", "method", "test"})

Source = Literal["vector", "keyword", "graph"]
SkipReason = Literal["contained", "budget", "limit"]


@dataclass(frozen=True, slots=True)
class Candidate:
    chunk_id: int
    node_key: str
    node_name: str
    qualified_name: str
    kind: str  # chunk kind: function, method, test, class, or module
    ref: EvidenceRef
    tokens: int
    fused: float = 0.0  # fused (or single-source) score; 0 if only the graph reached it
    expanded: float = 0.0  # graph expansion score of its node; 0 if not expanded
    sources: frozenset[Source] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class Ranked:
    candidate: Candidate
    score: float
    bonus: float


@dataclass(frozen=True, slots=True)
class QueryNames:
    """Identifiers in a question that could name a symbol, lower-cased."""

    plain: frozenset[str] = frozenset()
    dotted: frozenset[str] = frozenset()

    def match(self, name: str, qualified_name: str) -> bool:
        if name.lower() in self.plain:
            return True
        qualified = qualified_name.lower()
        return any(d == qualified or d.endswith("." + qualified) for d in self.dotted)


def query_names(question: str) -> QueryNames:
    plain: set[str] = set()
    dotted: set[str] = set()
    for identifier in identifiers(question):
        lowered = identifier.lower()
        if "." in lowered:
            dotted.add(lowered)
        elif not lowered.isdigit():
            plain.add(lowered)
    return QueryNames(frozenset(plain), frozenset(dotted))


def rerank(
    candidates: Iterable[Candidate], names: QueryNames, *, bonus: float = NAME_BONUS
) -> list[Ranked]:
    """Candidates by final score (ties by chunk ID)."""
    ranked = []
    for candidate in candidates:
        matched = names.match(candidate.node_name, candidate.qualified_name)
        extra = bonus if bonus and matched else 0.0
        ranked.append(Ranked(candidate, max(candidate.fused, candidate.expanded) + extra, extra))
    return sorted(ranked, key=lambda item: (-item.score, item.candidate.chunk_id))


@dataclass(frozen=True, slots=True)
class Packed:
    selected: list[Ranked]
    skipped: list[tuple[Ranked, SkipReason]]
    tokens: int


def pack(
    ranked: Sequence[Ranked], *, budget_tokens: int = DEFAULT_BUDGET_TOKENS, k: int | None = None
) -> Packed:
    """Select ranked chunks in order while they fit ``budget_tokens`` (and at most ``k``)."""
    selected: list[Ranked] = []
    skipped: list[tuple[Ranked, SkipReason]] = []
    used = 0
    for item in ranked:
        candidate = item.candidate
        if k is not None and len(selected) >= k:
            skipped.append((item, "limit"))
            continue
        if any(
            chosen.candidate.kind in CONTIGUOUS_KINDS
            and chosen.candidate.ref.contains(candidate.ref)
            for chosen in selected
        ):
            skipped.append((item, "contained"))
            continue
        if used + candidate.tokens > budget_tokens:
            skipped.append((item, "budget"))
            continue
        selected.append(item)
        used += candidate.tokens
    return Packed(selected=selected, skipped=skipped, tokens=used)
