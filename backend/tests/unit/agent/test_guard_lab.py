"""The detector pass on tool output, on the planted-injection fixture (GRD-3, GRD-5): whatever a
tool returns from a poisoned file reaches the model marked. No database, no model."""

from __future__ import annotations

import pytest

from repoatlas.agent.guard import evidence_line, mark_text, prepare_result
from repoatlas.agent.ledger import Ledger
from repoatlas.agent.results import ToolResult
from repoatlas.agent.tools import numbered
from repoatlas.guardrails.lab import load_lab
from repoatlas.retrieval.evidence import EvidenceRef
from repoatlas.store.blobs import line_count
from tests.unit.answers.helpers import FakeBlobs, oid

pytestmark = pytest.mark.injection_static

LAB = load_lab()
INDEXED = [variant for variant in LAB.variants if variant.indexed]


def read_file_output(path: str) -> str:
    """What ``read_file`` returns for a whole file of the fixture."""
    text = LAB.text(path)
    header = f"{path} lines 1-{line_count(text)} (the file has {line_count(text)} lines)."
    return f"{header}\n{numbered(text, 1)}"


@pytest.mark.parametrize("variant", INDEXED, ids=lambda variant: variant.id)
def test_every_planted_variant_is_marked_in_a_file_read(variant):
    marked_text, kinds = mark_text(read_file_output(variant.file))
    # A file's name is marked by its evidence entry (below); here, what is in the text.
    for kind in variant.expect:
        assert kind in kinds, (variant.id, kind, kinds)
    assert "<flagged reason=" in marked_text
    if not variant.encoded:
        # The canary sits inside a marked span, not in unmarked text.
        start = marked_text.index(variant.canary)
        opened = marked_text.rfind("<flagged reason=", 0, start)
        closed = marked_text.rfind("</flagged>", 0, start)
        assert opened > closed, variant.id


def test_an_instruction_split_over_two_numbered_comment_lines_is_still_found():
    split = LAB.variant("v11")
    text = read_file_output(split.file)
    assert " | " in text  # numbered: a gutter sits between the two comment lines
    _, kinds = mark_text(text)
    assert "instruction_to_assistant" in kinds


def test_a_file_whose_name_is_an_instruction_is_flagged_in_its_evidence_entry():
    variant = LAB.variant("v05")
    text = LAB.text(variant.file)
    ledger = Ledger(FakeBlobs({oid(text): text}))
    ref = EvidenceRef(blob_oid=oid(text), path=variant.file, start_line=1, end_line=5)
    [entry] = ledger.add([ref])
    [line] = ledger.lines([entry])
    for kind in variant.expect_path:
        assert kind in line, line
    assert line.startswith(f'<evidence id="E1" path="{variant.file}" lines="1-5" flags="')


def test_clean_fixture_code_is_not_marked():
    _, kinds = mark_text(read_file_output("src/lab/gateway.py"))
    assert kinds == []


def test_a_prepared_read_of_a_poisoned_file_keeps_its_marks_when_trimmed():
    variant = LAB.variant("v01")
    ref = EvidenceRef(blob_oid="a" * 40, path=variant.file, start_line=1, end_line=40)
    result = ToolResult(content=read_file_output(variant.file), evidence=[ref])
    prepared = prepare_result(result, [evidence_line("E1", ref)], max_tokens=200)
    assert prepared.truncated and "[Cut to fit" in prepared.text
    assert prepared.text.count("<flagged") == prepared.text.count("</flagged>")  # tags balanced
    assert prepared.text.rstrip().endswith(
        '<evidence id="E1" path="src/lab/billing.py" lines="1-40"/>'
    )
