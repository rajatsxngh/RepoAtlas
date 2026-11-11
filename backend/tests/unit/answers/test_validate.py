"""Citation validation: unknown IDs, out-of-range lines, symbols outside the range, partially
valid claims, and the pre-filter metrics."""

from __future__ import annotations

import pytest

from repoatlas.answers.schema import AnswerDraft, Claim, number_evidence
from repoatlas.answers.validate import (
    CitationValidator,
    contains_identifier,
    strip_markers,
    symbol_name,
)
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import EvidenceItem
from tests.unit.answers.helpers import EVIDENCE, FakeBlobs, oid

EVIDENCE_MAP = number_evidence(EVIDENCE)  # E1 create_app (4-9), E2 App (12-18), E3 slugify


def claim(cid: str, ids: list[str], symbol: str | None = None) -> Claim:
    return Claim(id=cid, text=f"claim {cid}", evidence_ids=ids, symbol=symbol)


def draft(*claims: Claim, markdown: str = "") -> AnswerDraft:
    return AnswerDraft(answer_markdown=markdown, claims=list(claims), refused=False)


def validate(*claims: Claim, markdown: str = "", evidence=EVIDENCE_MAP):
    return CitationValidator(FakeBlobs()).validate(draft(*claims, markdown=markdown), evidence)


def test_valid_citations_survive() -> None:
    report = validate(claim("c1", ["E1"], "create_app"), claim("c2", ["E2", "E3"]))
    assert [c.id for c in report.claims] == ["c1", "c2"]
    assert report.dropped_claims == []
    assert [(c.claim_id, c.evidence_id) for c in report.citations] == [
        ("c1", "E1"),
        ("c2", "E2"),
        ("c2", "E3"),
    ]
    assert report.citations[0].ref == EVIDENCE[0].ref
    assert report.metrics.prefilter_citation_validity == 1.0
    assert report.metrics.dropped_claim_rate == 0.0
    assert report.metrics.evidence_count == 3


def test_unknown_ids_are_removed() -> None:
    report = validate(claim("c1", ["E9"]), claim("c2", ["E1", "E7"]))
    assert [check.reason for check in report.checks] == ["unknown_id", None, "unknown_id"]
    assert [c.id for c in report.dropped_claims] == ["c1"]
    assert report.claims[0].evidence_ids == ["E1"]
    assert report.metrics.prefilter_citation_validity == pytest.approx(1 / 3)
    assert report.metrics.dropped_claim_rate == 0.5


def test_out_of_range_lines_and_missing_blobs() -> None:
    past_end = EvidenceItem.model_validate(
        {**EVIDENCE[2].model_dump(), "ref": {**EVIDENCE[2].ref.model_dump(), "end_line": 3}}
    )
    unknown_blob = EvidenceItem.model_validate(
        {**EVIDENCE[2].model_dump(), "ref": {**EVIDENCE[2].ref.model_dump(), "blob_oid": "f" * 40}}
    )
    evidence = number_evidence([past_end, unknown_blob])
    report = validate(claim("c1", ["E1"]), claim("c2", ["E2"]), evidence=evidence)
    assert [check.reason for check in report.checks] == ["range_missing", "blob_missing"]
    assert report.claims == []
    assert report.metrics.dropped_claim_rate == 1.0
    assert report.metrics.prefilter_citation_validity == 0.0


def test_the_last_line_of_a_file_is_in_range() -> None:
    assert EVIDENCE[2].ref.end_line == 2  # util.py has exactly 2 lines
    assert validate(claim("c1", ["E3"])).claims


def test_symbols_must_be_in_the_cited_range() -> None:
    report = validate(
        claim("c1", ["E1"], "register"),  # create_app calls app.register: present
        claim("c2", ["E3"], "create_app"),  # slugify's lines don't mention create_app
        claim("c3", ["E1", "E2"], "add_route"),  # only in App's range
        claim("c4", ["E2"], "`App.register()`"),
    )
    reasons = {(c.claim_id, c.evidence_id): c.reason for c in report.checks}
    assert reasons[("c1", "E1")] is None
    assert reasons[("c2", "E3")] == "symbol_not_in_range"
    assert reasons[("c3", "E1")] == "symbol_not_in_range"
    assert reasons[("c3", "E2")] is None
    assert reasons[("c4", "E2")] is None
    assert [c.id for c in report.claims] == ["c1", "c3", "c4"]
    assert report.claims[1].evidence_ids == ["E2"]  # partially valid: one citation kept


def test_symbols_match_whole_words_only() -> None:
    assert contains_identifier("def create_app(config):", "create_app")
    assert not contains_identifier("def create_application():", "create_app")
    assert not contains_identifier("my_create_app = 1", "create_app")
    assert contains_identifier("$el.on()", "on")
    assert contains_identifier("anything", "")
    assert symbol_name("`Router.add_route()`") == "add_route"
    assert symbol_name("pkg.mod:func") == "func"


def test_duplicate_citations_count_once() -> None:
    report = validate(claim("c1", ["E1", "E1"]))
    assert len(report.checks) == 1
    assert report.claims[0].evidence_ids == ["E1"]


def test_markers_of_dropped_claims_are_removed() -> None:
    report = validate(
        claim("c1", ["E1"]),
        claim("c2", ["E9"]),
        claim("c12", ["E9"]),
        markdown="It builds the app [c1]. It is fast [c2]. And more [c12][c1].",
    )
    assert report.answer_markdown == "It builds the app [c1]. It is fast. And more[c1]."
    assert strip_markers("x [c1]", set()) == "x [c1]"


def test_no_claims_means_no_rates() -> None:
    report = validate()
    assert report.metrics.prefilter_citation_validity is None
    assert report.metrics.dropped_claim_rate is None


def test_refs_keep_their_identity() -> None:
    ref = validate(claim("c1", ["E3"])).citations[0].ref
    assert ref == EvidenceRef(
        blob_oid=oid(FakeBlobs().blobs[ref.blob_oid]),
        path="src/util.py",
        start_line=1,
        end_line=2,
        symbol_key="py:src/util.py:slugify:function",
    )
