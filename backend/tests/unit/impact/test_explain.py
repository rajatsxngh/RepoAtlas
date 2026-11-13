"""Cited impact explanations (T7) with FakeLLM: validated citations and each refusal kind."""

from __future__ import annotations

import pytest

from repoatlas.answers.prompting import SYSTEM_PROMPT
from repoatlas.answers.schema import AnswerDraft, Budget, Claim
from repoatlas.impact.data import MemoryImpactData
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.explain import NOTHING_TEXT, ImpactExplainer, explain_impact
from repoatlas.impact.schema import ImpactReport, SymbolTarget
from repoatlas.ingest.source import blob_oid
from repoatlas.llm.fake import FakeLLM, refusal
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.tracing.core import Trace
from tests.unit.impact.helpers import MAIN, PARSE, PROJECT, TEST_PARSE, TOKENIZE, project_data

pytestmark = pytest.mark.anyio


class Blobs:
    def __init__(self) -> None:
        self.texts = {blob_oid(text.encode()): text for text in PROJECT.values()}

    def text(self, oid: str) -> str | None:
        return self.texts.get(oid)


class Sink:
    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def write(self, trace: Trace) -> None:
        self.traces.append(trace)


def setup(llm: FakeLLM, sink: Sink | None = None) -> tuple[MemoryImpactData, ImpactExplainer]:
    data = project_data()
    return data, ImpactExplainer(data, Blobs(), llm, prices=PriceTable(DEFAULT_PRICES), sink=sink)


def report_for(data: MemoryImpactData, key: str) -> ImpactReport:
    return ImpactEngine(data).analyze(1, SymbolTarget(symbol_key=key))


DRAFT = AnswerDraft(
    answer_markdown="Changing `_tokenize` affects `parse` [c1], tested by `test_parse` [c2]. "
    "Also `cli` [c3].",
    claims=[
        Claim(id="c1", text="parse calls _tokenize.", evidence_ids=["E2"], symbol="parse"),
        Claim(id="c2", text="test_parse calls parse.", evidence_ids=["E4"], symbol="test_parse"),
        Claim(id="c3", text="The CLI breaks.", evidence_ids=["E9"], symbol="main"),
    ],
    refused=False,
)


async def test_an_explanation_cites_the_reports_evidence_and_drops_bad_citations() -> None:
    llm = FakeLLM([DRAFT])
    sink = Sink()
    data, explainer = setup(llm, sink)
    report = report_for(data, TOKENIZE)
    evidence = explainer.evidence_items(1, report)
    assert [e.node_key for e in evidence] == [TOKENIZE, PARSE, MAIN, TEST_PARSE]
    assert evidence[1].text.startswith("def parse(text):")
    assert (evidence[1].ref.start_line, evidence[1].ref.end_line) == (5, 6)

    result = await explain_impact(report, snapshot_id=1, explainer=explainer)
    assert not result.refused
    assert [c.id for c in result.claims] == ["c1", "c2"]
    assert [(c.claim_id, c.evidence_id) for c in result.citations] == [("c1", "E2"), ("c2", "E4")]
    assert result.metrics.dropped_claim_rate == pytest.approx(1 / 3)
    (call,) = llm.calls
    assert call.role == "large" and call.schema is AnswerDraft
    assert call.messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    question = call.messages[1]["content"][1]["text"]
    assert f"`{TOKENIZE}`" in question
    assert "Public apis:" in question and "Tests:" in question and "Callers:" in question
    assert "may refer to it" in question  # the unknown sites are mentioned, not dropped
    (trace,) = sink.traces
    assert trace.kind == "impact_explain" and result.trace_id == trace.id


async def test_a_report_that_reaches_nothing_is_refused_without_a_call() -> None:
    llm = FakeLLM([])
    data, explainer = setup(llm)
    report = report_for(data, MAIN)
    assert report.items == []
    result = await explainer.explain(1, report)
    assert result.refused and result.refusal_kind == "evidence"
    assert result.answer_markdown == NOTHING_TEXT
    assert llm.calls == []


async def test_model_refusals_and_budgets() -> None:
    llm = FakeLLM([refusal()])
    data, explainer = setup(llm)
    report = report_for(data, TOKENIZE)
    declined = await explainer.explain(1, report)
    assert declined.refused and declined.refusal_kind == "api"
    expensive = await explainer.explain(1, report, Budget(max_cost_usd=1e-9))
    assert expensive.refused and expensive.refusal_kind == "budget"
    assert len(llm.calls) == 1


async def test_an_answer_with_no_valid_claim_is_an_evidence_refusal() -> None:
    bad = AnswerDraft(
        answer_markdown="Everything breaks [c1].",
        claims=[Claim(id="c1", text="All of it.", evidence_ids=["E1"], symbol="nonexistent")],
        refused=False,
    )
    data, explainer = setup(FakeLLM([bad]))
    result = await explainer.explain(1, report_for(data, TOKENIZE))
    assert result.refused and result.refusal_kind == "evidence"
    assert result.claims == []


def test_items_per_group_are_capped() -> None:
    data, explainer = setup(FakeLLM([]))
    explainer.per_group = 0
    report = report_for(data, TOKENIZE)
    assert explainer.chosen_items(report) == []
    assert [e.node_key for e in explainer.evidence_items(1, report)] == [TOKENIZE]
