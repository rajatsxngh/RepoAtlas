"""Evidence references: validation, the compact form, and containment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from repoatlas.retrieval.evidence import EvidenceRef

OID = "abc1234" + "0" * 33


def ref(start: int = 10, end: int = 24, path: str = "src/app/routes.py", oid: str = OID):
    return EvidenceRef(blob_oid=oid, path=path, start_line=start, end_line=end)


def test_compact_form_matches_the_prd() -> None:
    assert ref().to_compact() == "src/app/routes.py#L10-L24@abc1234"
    assert str(ref(3, 3)) == "src/app/routes.py#L3-L3@abc1234"


def test_compact_form_round_trips_with_the_full_oid() -> None:
    original = ref().model_copy(update={"symbol_key": "py:src/app/routes.py:f:function"})
    back = EvidenceRef.from_compact(
        original.to_compact(), blob_oid=OID, symbol_key=original.symbol_key
    )
    assert back == original
    assert not back.abbreviated


def test_compact_form_alone_keeps_the_abbreviated_oid() -> None:
    back = EvidenceRef.from_compact("src/app/routes.py#L10-L24@abc1234")
    assert back.blob_oid == "abc1234"
    assert back.abbreviated
    assert back.same_blob(ref())


@pytest.mark.parametrize(
    ("text", "path", "start", "end"),
    [
        ("a.py#L5@abc1234", "a.py", 5, 5),
        ("dir with space/x#y.ts#L1-L2@abc1234", "dir with space/x#y.ts", 1, 2),
        ("  a.py#L1-L9@abc1234  ", "a.py", 1, 9),
    ],
)
def test_from_compact_parses_edge_cases(text: str, path: str, start: int, end: int) -> None:
    parsed = EvidenceRef.from_compact(text)
    assert (parsed.path, parsed.start_line, parsed.end_line) == (path, start, end)


@pytest.mark.parametrize(
    "text",
    [
        "a.py",
        "a.py#L1-L2",
        "a.py#L1-L2@xyz",
        "#L1-L2@abc1234",
        "a.py#L0-L2@abc1234",
        "a.py#L5-L2@abc1234",
    ],
)
def test_from_compact_rejects_malformed_references(text: str) -> None:
    with pytest.raises(ValueError):
        EvidenceRef.from_compact(text)


def test_from_compact_rejects_a_full_oid_that_does_not_match() -> None:
    with pytest.raises(ValueError, match="does not match"):
        EvidenceRef.from_compact("a.py#L1-L2@abc1234", blob_oid="f" * 40)


def test_lines_are_one_based_and_ordered() -> None:
    with pytest.raises(ValidationError):
        ref(0, 3)
    with pytest.raises(ValidationError):
        ref(5, 4)
    with pytest.raises(ValidationError):
        ref(oid="ABC1234")
    assert ref(5, 5).line_count == 1


def test_containment_and_overlap_need_the_same_blob_and_path() -> None:
    outer = ref(10, 30)
    assert outer.contains(ref(12, 20))
    assert outer.contains(outer)
    assert not outer.contains(ref(5, 20))
    assert not outer.contains(ref(12, 20, path="other.py"))
    assert not outer.contains(ref(12, 20, oid="f" * 40))
    assert outer.overlaps(ref(25, 40))
    assert not outer.overlaps(ref(31, 40))


def test_references_are_frozen_and_hashable() -> None:
    assert len({ref(), ref(), ref(1, 2)}) == 2
    with pytest.raises(ValidationError):
        ref().start_line = 3
