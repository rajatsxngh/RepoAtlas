"""Mapping system output to symbol keys on the evaluated snapshot (M10 design notes).

Ground truth names symbols by key, so a cited range has to become a key:

- **A citation's symbol** is the reference's own ``symbol_key`` when retrieval set one (chunks of
  a symbol carry it), else the innermost class, function, method, or test whose span contains
  the range's first line — the same rule as ``GraphStore.locate`` — else the file.
- **A claim's answer keys** (the predicted set for list questions) come from claims tagged with a
  ``symbol``: the cited symbols whose name matches the tag's last identifier (a symbol named in
  a cited range, the containing symbol, or one of its parents). A tag that matches nothing it
  cites names no key.
- **Without citations** (closed book), a tag is resolved by name over the whole snapshot; when
  several symbols share the name, the paths the claim mentions break the tie, and a tie left
  over names no key.

``SymbolIndex`` holds what this needs for one snapshot (each node's key, name, kind, path, name
line, and span), built from the stored graph once per snapshot.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import Engine

from repoatlas.answers.schema import AnswerResult
from repoatlas.evals.outputs import Outcome, PredictedClaim, SystemOutput
from repoatlas.graph.model import CodeGraph, Node
from repoatlas.retrieval.evidence import EvidenceRef

LOCATABLE = ("class", "function", "method", "test")
_KIND_ORDER = {"method": 0, "function": 1, "test": 2, "class": 3}
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


@dataclass(frozen=True, slots=True)
class SymbolEntry:
    key: str
    name: str
    qualified_name: str
    kind: str
    path: str
    name_line: int | None
    start: int | None
    end: int | None
    parent: str | None


def entry_for(node: Node) -> SymbolEntry:
    return SymbolEntry(
        key=node.key,
        name=node.name,
        qualified_name=node.qualified_name or node.name,
        kind=str(node.kind),
        path=node.path or "",
        name_line=node.name_range.start_line if node.name_range else None,
        start=node.span.start_line if node.span else None,
        end=node.span.end_line if node.span else None,
        parent=node.parent_key,
    )


def tag_name(symbol: str | None) -> str | None:
    """The last identifier of a claim's symbol tag: ``Router.add_route()`` → ``add_route``."""
    if not symbol:
        return None
    names = _IDENT.findall(symbol)
    return names[-1] if names else None


class SymbolIndex:
    """Symbols of one snapshot by path and by name."""

    def __init__(self, entries: Iterable[SymbolEntry]) -> None:
        self.by_key: dict[str, SymbolEntry] = {}
        self.by_path: dict[str, list[SymbolEntry]] = defaultdict(list)
        self.by_name: dict[str, list[SymbolEntry]] = defaultdict(list)
        for entry in entries:
            self.by_key[entry.key] = entry
            if entry.kind in LOCATABLE:
                self.by_path[entry.path].append(entry)
                self.by_name[entry.name].append(entry)
        self.files = {e.path: e.key for e in self.by_key.values() if e.kind == "file"}

    @classmethod
    def from_graph(cls, graph: CodeGraph) -> SymbolIndex:
        return cls(entry_for(node) for node in graph.nodes.values())

    def locate(self, path: str, line: int) -> str | None:
        """The innermost symbol whose span contains ``line``, else the file's key."""
        containing = [
            e
            for e in self.by_path.get(path, [])
            if e.start is not None and e.end is not None and e.start <= line <= e.end
        ]
        if not containing:
            return self.files.get(path)
        containing.sort(
            key=lambda e: (-(e.start or 0), e.end or 0, _KIND_ORDER.get(e.kind, 9), e.key)
        )
        return containing[0].key

    def key_for_ref(self, ref: EvidenceRef) -> str | None:
        if ref.symbol_key and ref.symbol_key in self.by_key:
            return ref.symbol_key
        return self.locate(ref.path, ref.start_line)

    def _lineage(self, key: str | None) -> list[SymbolEntry]:
        out: list[SymbolEntry] = []
        while key is not None and key in self.by_key and len(out) < 20:
            entry = self.by_key[key]
            if entry.kind not in LOCATABLE:
                break
            out.append(entry)
            key = entry.parent
        return out

    def claim_keys(self, symbol: str | None, refs: list[EvidenceRef], text: str = "") -> list[str]:
        name = tag_name(symbol)
        if name is None:
            return []
        if not refs:
            return self.resolve_name(name, text)
        found: list[str] = []
        for ref in refs:
            named = [
                e.key
                for e in self.by_path.get(ref.path, [])
                if e.name == name
                and e.name_line is not None
                and ref.start_line <= e.name_line <= ref.end_line
            ]
            if not named:
                named = [e.key for e in self._lineage(self.key_for_ref(ref)) if e.name == name]
            found.extend(named)
        return list(dict.fromkeys(found))

    def resolve_name(self, name: str, text: str = "") -> list[str]:
        candidates = self.by_name.get(name, [])
        if len(candidates) == 1:
            return [candidates[0].key]
        mentioned = [e for e in candidates if e.path and e.path in text]
        if len(mentioned) == 1:
            return [mentioned[0].key]
        return []


def _outcome(result: AnswerResult) -> Outcome:
    if not result.refused:
        return "answered"
    return {"evidence": "refused_evidence", "api": "refused_api", "budget": "refused_budget"}.get(
        result.refusal_kind or "evidence", "refused_evidence"
    )  # type: ignore[return-value]


def answer_output(result: AnswerResult, index: SymbolIndex, latency_s: float) -> SystemOutput:
    """``result`` as a ``SystemOutput`` with every citation and claim mapped to keys."""
    refs_by_claim: dict[str, list[EvidenceRef]] = defaultdict(list)
    citations: list[EvidenceRef] = []
    for citation in result.citations:
        refs_by_claim[citation.claim_id].append(citation.ref)
        if citation.ref not in citations:
            citations.append(citation.ref)
    claims = [
        PredictedClaim(
            id=claim.id, text=claim.text, symbol=claim.symbol, refs=refs_by_claim.get(claim.id, [])
        )
        for claim in result.claims
    ]
    metrics = result.metrics
    output = SystemOutput(
        kind="answer",
        outcome=_outcome(result),
        answer_markdown=result.answer_markdown,
        refusal_reason=result.refusal_reason,
        claims=claims,
        citations=citations,
        prefilter_citation_validity=metrics.prefilter_citation_validity,
        dropped_claim_rate=metrics.dropped_claim_rate,
        citations_total=metrics.citations_total,
        citations_valid=metrics.citations_valid,
        claims_total=metrics.claims_total,
        claims_dropped=metrics.claims_dropped,
        evidence_count=metrics.evidence_count,
        cost_usd=result.cost_usd,
        latency_s=round(latency_s, 3),
        input_tokens=result.usage.total_input_tokens,
        output_tokens=result.usage.output_tokens,
    )
    return remap(output, index)


def remap(output: SystemOutput, index: SymbolIndex) -> SystemOutput:
    """Recompute every symbol key from the output's claims and citations. Runs on cached
    outputs too, so a change to the mapping never needs the answers again."""
    if output.kind != "answer":
        return output
    claims = [
        claim.model_copy(
            update={
                "keys": index.claim_keys(claim.symbol, claim.refs, claim.text),
                "ref_keys": list(
                    dict.fromkeys(k for r in claim.refs if (k := index.key_for_ref(r)) is not None)
                ),
            }
        )
        for claim in output.claims
    ]
    cited = [key for ref in output.citations if (key := index.key_for_ref(ref)) is not None]
    return output.model_copy(
        update={
            "claims": claims,
            "cited_keys": list(dict.fromkeys(cited)),
            "answer_keys": list(dict.fromkeys(k for claim in claims for k in claim.keys)),
        }
    )


class GraphIndexes:
    """``SymbolIndex`` per snapshot, loaded from the store once and kept."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._indexes: dict[int, SymbolIndex] = {}
        self._lock = threading.Lock()

    def __call__(self, snapshot_id: int) -> SymbolIndex:
        with self._lock:
            index = self._indexes.get(snapshot_id)
            if index is None:
                from repoatlas.store.loader import load_graph

                index = SymbolIndex.from_graph(load_graph(snapshot_id, engine=self.engine))
                self._indexes[snapshot_id] = index
            return index
