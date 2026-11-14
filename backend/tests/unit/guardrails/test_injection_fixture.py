"""The planted-injection fixture (GRD-4) and the detector's recall on it (GRD-5): every variant
is there with its own canary, the detector marks each one where it sits, and every variant that
RepoAtlas indexes reaches a prompt layout wrapped as flagged."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from repoatlas.answers.prompting import evidence_block
from repoatlas.answers.schema import number_evidence
from repoatlas.guardrails.detector import kinds, marked, scan, scan_path
from repoatlas.guardrails.lab import (
    LAB_NAME,
    Lab,
    Variant,
    canary_pattern,
    find_canaries,
    lab_dir,
    load_lab,
)
from repoatlas.ingest.inventory import language_for
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.retrieval.retriever import EvidenceItem

pytestmark = pytest.mark.injection_static

LAB = load_lab()
VARIANTS = LAB.variants
# The techniques the requirement and the design notes name; each must be planted.
REQUIRED_KINDS = {
    "code_comment",
    "docstring_fake_system_block",
    "readme_section_for_the_ai",
    "string_literal",
    "file_name",
    "markdown_link_fetch",
    "test_name",
    "cite_missing_file",
    "claim_safe_to_delete",
    "base64_comment",
    "split_across_comments",
    "fake_tool_call_json",
    "role_play",
    "non_english",
    "hidden_in_long_docstring",
}


def ids(variants: list[Variant]) -> list[str]:
    return [f"{variant.id}-{variant.kind}" for variant in variants]


def test_the_fixture_has_every_planned_variant_with_its_own_canary() -> None:
    assert lab_dir().name == LAB_NAME and LAB.root == lab_dir()
    assert len(VARIANTS) >= 15
    assert {variant.kind for variant in VARIANTS} >= REQUIRED_KINDS
    assert [variant.id for variant in VARIANTS] == sorted(variant.id for variant in VARIANTS)
    canaries = [variant.canary for variant in VARIANTS]
    assert len(set(canaries)) == len(canaries)
    # A canary names its variant, so a leak points at its source.
    assert all(variant.canary.split("-")[1] == variant.id.upper() for variant in VARIANTS)
    # Only source files are indexed: the README variant can't reach a prompt.
    assert [v.id for v in VARIANTS if not v.indexed] == ["v03"]
    for variant in VARIANTS:
        assert (language_for(variant.file) is not None) == variant.indexed, variant.id


@pytest.mark.parametrize("variant", VARIANTS, ids=ids(VARIANTS))
def test_each_canary_is_planted_in_its_file_only(variant: Variant) -> None:
    for path in LAB.files:
        if path in ("variants.yaml", "questions.yaml"):
            continue
        text = LAB.text(path)
        plain = variant.canary in text
        if variant.encoded:
            decoded = " ".join(
                base64.b64decode(run + "=" * (-len(run) % 4)).decode("utf-8", "replace")
                for run in re.findall(r"[A-Za-z0-9+/]{40,}", text)
            )
            assert not plain, f"{variant.id}: the canary must only be inside the payload"
            assert (variant.canary in decoded) == (path == variant.file), path
        else:
            assert plain == (path == variant.file), path


@pytest.mark.parametrize("variant", VARIANTS, ids=ids(VARIANTS))
def test_the_detector_marks_every_variant(variant: Variant) -> None:
    text = LAB.text(variant.file)
    found = marked(scan(text))
    assert set(variant.expect) <= set(kinds(found)), kinds(found)
    assert set(variant.expect_path) <= set(kinds(marked(scan_path(variant.file))))
    assert variant.expect or variant.expect_path, "every variant must be detectable"
    # A marked finding sits within a few lines of the variant's own canary (or payload).
    anchor = "SWdub3Jl" if variant.encoded else variant.canary
    line = text[: text.index(anchor)].count("\n")
    near = [f for f in found if abs(text[: f.span[0]].count("\n") - line) <= 4]
    assert set(variant.expect) <= set(kinds(near)), (variant.id, kinds(near))


def test_recall_on_the_fixture_is_complete() -> None:
    caught = [
        variant.id
        for variant in VARIANTS
        if set(variant.expect) <= set(kinds(marked(scan(LAB.text(variant.file)))))
        and set(variant.expect_path) <= set(kinds(marked(scan_path(variant.file))))
    ]
    assert len(caught) == len(VARIANTS) == 17


def test_clean_files_of_the_fixture_are_not_marked() -> None:
    planted = {variant.file for variant in VARIANTS}
    clean = [path for path in LAB.files if path.endswith((".py", ".ts")) and path not in planted]
    assert clean == ["src/lab/gateway.py"]
    for path in clean:
        assert marked(scan(LAB.text(path))) == []
        assert marked(scan_path(path)) == []


def function_text(variant: Variant) -> tuple[str, int, int]:
    """The lines of the variant's function (to the next top-level definition)."""
    lines = LAB.text(variant.file).split("\n")
    assert variant.symbol is not None
    start = next(
        index
        for index, line in enumerate(lines)
        if re.match(rf"(?:export\s+)?(?:def|function)\s+{re.escape(variant.symbol)}\b", line)
    )
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.match(r"(?:def |class |export |@|/\*\*)", lines[index])
        ),
        len(lines),
    )
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end]), start + 1, end


INDEXED = [variant for variant in VARIANTS if variant.indexed]


@pytest.mark.parametrize("variant", INDEXED, ids=ids(INDEXED))
def test_each_indexed_variant_is_wrapped_as_flagged_in_an_evidence_block(variant: Variant) -> None:
    text, start, end = function_text(variant)
    item = EvidenceItem(
        ref=EvidenceRef(blob_oid="a" * 40, path=variant.file, start_line=start, end_line=end),
        text=text,
        score=1.0,
        sources={"keyword"},
        node_key=f"file:{variant.file}",
    )
    numbered = number_evidence([item])["E1"]
    block = evidence_block(numbered)
    header = block.splitlines()[0]
    for kind in [*variant.expect, *variant.expect_path]:
        assert kind in header.split('flags="')[1], (variant.id, header)
    wrapped = " ".join(re.findall(r"<flagged reason=\"[a-z_,]+\">(.*?)</flagged>", block, re.S))
    if variant.encoded:
        assert "SWdub3Jl" in wrapped
    else:
        assert variant.canary in wrapped  # the canary itself is inside a flagged span


def test_canary_patterns_match_however_an_answer_writes_them() -> None:
    pattern = canary_pattern("CANARY-V01-7F3A")
    for written in (
        "CANARY-V01-7F3A",
        "canary-v01-7f3a",
        "CANARY_V01_7F3A",
        "Canary V01 7F3A",
        "CANARYV017F3A",
        "`CANARY`-`V01`-`7F3A`",
        "the code is CANARY-V01-7F3A.",
    ):
        assert pattern.search(written), written
    assert not pattern.search("CANARY-V02-91BD")
    assert not pattern.search("a canary in a coal mine, v01")
    assert find_canaries("x CANARY-V01-7F3A y CANARY-V09-E55B x CANARY-V01-7F3A") == [
        "CANARY-V01-7F3A",
        "CANARY-V09-E55B",
    ]


def test_questions_make_every_indexed_variant_relevant_and_ask_for_nothing_planted() -> None:
    asked = {variant for question in LAB.questions for variant in question.variants}
    assert asked == {variant.id for variant in INDEXED}
    oneshot, impact = LAB.questions_for("oneshot"), LAB.questions_for("impact_explain")
    assert len(oneshot) >= 12 and len(impact) >= 4
    for question in LAB.questions:
        text = f"{question.text or ''} {question.symbol or ''}"
        assert "canary" not in text.lower()
        for variant in VARIANTS:
            for marker in variant.markers:
                assert not re.search(marker.pattern, text, re.IGNORECASE), question.id
    for question in impact:
        assert question.symbol and re.search(
            rf"(?:def|function)\s+{re.escape(question.symbol.split('.')[-1])}\b",
            LAB.text(question.path or ""),
        )


def test_a_broken_fixture_is_refused(tmp_path: Path) -> None:
    def copy(variants: str, questions: str) -> Path:
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        (root / "src").mkdir(parents=True)
        (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (root / "variants.yaml").write_text(variants, encoding="utf-8")
        (root / "questions.yaml").write_text(questions, encoding="utf-8")
        return root

    def variants(count: int, canary: str = "CANARY-V{n:02d}-00AA") -> str:
        rows = [
            f"  - {{id: v{n:02d}, kind: k, file: src/a.py, canary: {canary.format(n=n)}}}"
            for n in range(1, count + 1)
        ]
        return "variants:\n" + "\n".join(rows) + "\n"

    good_questions = "version: 1\nquestions:\n  - {id: q1, mode: oneshot, text: What}\n"
    assert isinstance(load_lab(copy(variants(15), good_questions)), Lab)
    with pytest.raises(ValueError, match="at least 15"):
        load_lab(copy(variants(14), good_questions))
    with pytest.raises(ValueError, match="distinct"):
        load_lab(copy(variants(15, canary="CANARY-V01-00AA"), good_questions))
    unknown = "version: 1\nquestions:\n  - {id: q1, mode: oneshot, text: W, variants: [v99]}\n"
    with pytest.raises(ValueError, match="unknown variants"):
        load_lab(copy(variants(15), unknown))
    with pytest.raises(ValueError, match="needs symbol and path"):
        load_lab(copy(variants(15), "version: 1\nquestions:\n  - {id: q, mode: impact_explain}\n"))
