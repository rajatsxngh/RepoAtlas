"""Citation validation (ANS-8, decision 0011).

For each claim and each evidence ID it cites:

1. the ID must be one of the evidence IDs the model was given (``unknown_id``);
2. the evidence's blob must be stored (``blob_missing``) and its line range must exist in the
   blob, by the line count of the stored text (``range_missing``);
3. if the claim names a symbol, the cited lines must contain that identifier as a whole word
   (``symbol_not_in_range``; for a dotted name like ``Router.add_route``, the last part).

Invalid citations are removed; a claim with no valid citation left is dropped, and its
``[cN]`` markers are removed from the answer. ``prefilter_citation_validity`` (valid citations
÷ all citations) and ``dropped_claim_rate`` (dropped claims ÷ all claims) are measured before
anything is removed, so they are honest even though every shown citation is valid.

Blob text is read through the store's ``blobs`` table (``StoreBlobs``), never from a checkout.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import Engine, text

from repoatlas.answers.schema import AnswerDraft, AnswerMetrics, Citation, Claim, NumberedEvidence
from repoatlas.store.blobs import line_count, split_lines

Reason = Literal["unknown_id", "blob_missing", "range_missing", "symbol_not_in_range"]


class BlobSource(Protocol):
    def text(self, blob_oid: str) -> str | None: ...


class StoreBlobs:
    """Blob text from the store's ``blobs`` table, cached per instance."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._cache: dict[str, str | None] = {}

    def text(self, blob_oid: str) -> str | None:
        if blob_oid not in self._cache:
            with self.engine.connect() as connection:
                self._cache[blob_oid] = connection.execute(
                    text("SELECT text FROM blobs WHERE oid = :oid"), {"oid": blob_oid}
                ).scalar_one_or_none()
        return self._cache[blob_oid]


class CitationCheck(BaseModel):
    claim_id: str
    evidence_id: str
    valid: bool
    reason: Reason | None = None


class ValidationReport(BaseModel):
    claims: list[Claim]  # the surviving claims, with only their valid evidence IDs
    dropped_claims: list[Claim]  # as the model wrote them
    citations: list[Citation]
    checks: list[CitationCheck]
    answer_markdown: str  # without the markers of dropped claims
    metrics: AnswerMetrics


def symbol_name(symbol: str) -> str:
    """The identifier to look for: backticks, a trailing ``()``, and dotted prefixes removed."""
    name = symbol.strip().strip("`").strip()
    name = re.sub(r"\(.*\)$", "", name).strip()
    return re.split(r"[.:#]+", name)[-1] if name else ""


def contains_identifier(code: str, symbol: str) -> bool:
    name = symbol_name(symbol)
    if not name:
        return True
    return re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", code) is not None


class CitationValidator:
    def __init__(self, blobs: BlobSource) -> None:
        self.blobs = blobs

    def validate(
        self, draft: AnswerDraft, evidence_map: dict[str, NumberedEvidence]
    ) -> ValidationReport:
        checks: list[CitationCheck] = []
        kept: list[Claim] = []
        dropped: list[Claim] = []
        citations: list[Citation] = []
        for claim in draft.claims:
            valid_ids: list[str] = []
            for evidence_id in dict.fromkeys(claim.evidence_ids):  # cited twice counts once
                reason = self._check(claim, evidence_id, evidence_map)
                checks.append(
                    CitationCheck(
                        claim_id=claim.id,
                        evidence_id=evidence_id,
                        valid=reason is None,
                        reason=reason,
                    )
                )
                if reason is None:
                    valid_ids.append(evidence_id)
            if valid_ids:
                kept.append(claim.model_copy(update={"evidence_ids": valid_ids}))
                citations.extend(
                    Citation(claim_id=claim.id, evidence_id=eid, ref=evidence_map[eid].ref)
                    for eid in valid_ids
                )
            else:
                dropped.append(claim)
        total = len(checks)
        valid = sum(check.valid for check in checks)
        metrics = AnswerMetrics(
            prefilter_citation_validity=valid / total if total else None,
            dropped_claim_rate=len(dropped) / len(draft.claims) if draft.claims else None,
            citations_total=total,
            citations_valid=valid,
            claims_total=len(draft.claims),
            claims_dropped=len(dropped),
            evidence_count=len(evidence_map),
        )
        return ValidationReport(
            claims=kept,
            dropped_claims=dropped,
            citations=citations,
            checks=checks,
            answer_markdown=strip_markers(draft.answer_markdown, {c.id for c in dropped}),
            metrics=metrics,
        )

    def _check(
        self, claim: Claim, evidence_id: str, evidence_map: dict[str, NumberedEvidence]
    ) -> Reason | None:
        evidence = evidence_map.get(evidence_id.strip())
        if evidence is None:
            return "unknown_id"
        ref = evidence.ref
        blob = self.blobs.text(ref.blob_oid)
        if blob is None:
            return "blob_missing"
        if ref.end_line > line_count(blob):
            return "range_missing"
        if claim.symbol:
            lines = split_lines(blob)[ref.start_line - 1 : ref.end_line]
            if not contains_identifier("\n".join(lines), claim.symbol):
                return "symbol_not_in_range"
        return None


def strip_markers(markdown: str, claim_ids: set[str]) -> str:
    """``markdown`` without ``[id]`` markers for ``claim_ids``."""
    if not claim_ids:
        return markdown
    pattern = "|".join(re.escape(claim_id) for claim_id in sorted(claim_ids, key=len, reverse=True))
    return re.sub(rf" ?\[(?:{pattern})\]", "", markdown)
