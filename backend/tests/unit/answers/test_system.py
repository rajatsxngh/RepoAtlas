"""The one-shot answer system with ``FakeLLM``: answers, each refusal kind, traces, registry."""

from __future__ import annotations

from typing import Any

import pytest

from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.prompting import SYSTEM_PROMPT
from repoatlas.answers.schema import AnswerDraft, Budget, Claim
from repoatlas.answers.system import (
    NO_EVIDENCE_TEXT,
    UNVERIFIED_TEXT,
    OneShotAnswerSystem,
    answer_systems,
)
from repoatlas.answers.validate import CitationValidator
from repoatlas.llm.fake import FakeLLM, refusal
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.retrieval.retriever import EvidenceItem, RetrievalDebug, RetrievalResult
from repoatlas.tracing.core import Trace
from tests.unit.answers.helpers import EVIDENCE, FakeBlobs

pytestmark = pytest.mark.anyio
QUESTION = "How is the app created?"


class FakeRetriever:
    def __init__(self, evidence: list[EvidenceItem]) -> None:
        self.evidence = evidence
        self.calls: list[tuple[int, str, str]] = []

    def retrieve(
        self, snapshot_id: int, query: str, mode: Any = "hybrid", budget_tokens: int = 12_000
    ) -> RetrievalResult:
        self.calls.append((snapshot_id, query, mode))
        debug = RetrievalDebug(
            query=query,
            mode=mode,
            model=None,
            keyword_query=None,
            budget_tokens=budget_tokens,
            k=20,
        )
        return RetrievalResult(evidence=self.evidence, debug=debug)


class Sink:
    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def write(self, trace: Trace) -> None:
        self.traces.append(trace)


def strong(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return [EvidenceItem.model_validate({**i.model_dump(), "score": 0.05}) for i in items]


def system(
    llm: FakeLLM, evidence: list[EvidenceItem] | None = None, sink: Sink | None = None
) -> OneShotAnswerSystem:
    return OneShotAnswerSystem(
        FakeRetriever(strong(EVIDENCE) if evidence is None else evidence),
        llm,
        CitationValidator(FakeBlobs()),
        RefusalPolicy(min_retrieval_score=0.02),
        prices=PriceTable(DEFAULT_PRICES),
        sink=sink,
    )


DRAFT = AnswerDraft(
    answer_markdown="`create_app` builds an `App` [c1] and registers routes [c2]. Fast [c3].",
    claims=[
        Claim(id="c1", text="create_app builds an App.", evidence_ids=["E1"], symbol="create_app"),
        Claim(
            id="c2", text="App.register adds routes.", evidence_ids=["E2", "E9"], symbol="register"
        ),
        Claim(id="c3", text="It is fast.", evidence_ids=["E7"]),
    ],
    refused=False,
)


async def test_an_answer_with_validated_citations() -> None:
    llm = FakeLLM([DRAFT])
    sink = Sink()
    steps: list[str] = []

    async def on_status(step: str) -> None:
        steps.append(step)

    result = await system(llm, sink=sink).answer(7, QUESTION, Budget(), on_status=on_status)
    assert steps == ["retrieving", "answering", "validating"]
    assert not result.refused
    assert [c.id for c in result.claims] == ["c1", "c2"]
    assert result.claims[1].evidence_ids == ["E2"]
    assert [(c.claim_id, c.evidence_id) for c in result.citations] == [("c1", "E1"), ("c2", "E2")]
    assert result.answer_markdown.endswith("[c2]. Fast.")
    assert result.metrics.prefilter_citation_validity == pytest.approx(2 / 4)
    assert result.metrics.dropped_claim_rate == pytest.approx(1 / 3)
    assert result.metrics.evidence_count == 3
    assert result.metrics.retrieval_top_score == 0.05
    assert result.usage == llm.usage
    assert result.cost_usd > 0
    assert result.draft == DRAFT
    # One call to the large model with the answer prompt and schema.
    (call,) = llm.calls
    assert call.role == "large"
    assert call.schema is AnswerDraft
    assert call.messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert QUESTION in call.messages[1]["content"][1]["text"]
    # The trace was written with the answer's ID and its steps.
    (trace,) = sink.traces
    assert result.trace_id == trace.id
    assert trace.status == "ok"
    assert trace.snapshot_id == 7
    names = [span.name for span in trace.spans]
    assert names == ["answer", "retrieve", "validate"]
    root = trace.spans[0]
    assert root.attrs["claims"] == 2
    assert root.attrs["refusal_kind"] is None


async def test_weak_retrieval_refuses_without_calling_the_llm() -> None:
    llm = FakeLLM([])
    weak = [EvidenceItem.model_validate({**EVIDENCE[0].model_dump(), "score": 0.01})]
    result = await system(llm, evidence=weak).answer(1, QUESTION, Budget())
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert result.answer_markdown == NO_EVIDENCE_TEXT
    assert result.metrics.retrieval_top_score == 0.01
    assert result.cost_usd == 0
    assert result.trace_id is not None
    assert llm.calls == []


async def test_no_evidence_refuses_without_calling_the_llm() -> None:
    llm = FakeLLM([])
    result = await system(llm, evidence=[]).answer(1, QUESTION, Budget())
    assert result.refusal_kind == "evidence"
    assert llm.calls == []


async def test_a_call_over_budget_is_not_made() -> None:
    llm = FakeLLM([])
    result = await system(llm).answer(1, QUESTION, Budget(max_cost_usd=0.001))
    assert (result.refused, result.refusal_kind) == (True, "budget")
    assert llm.calls == []


async def test_a_model_refusal_is_an_api_refusal() -> None:
    result = await system(FakeLLM([refusal()])).answer(1, QUESTION, Budget())
    assert (result.refused, result.refusal_kind) == (True, "api")
    assert result.usage.input_tokens == 100


async def test_the_model_saying_the_evidence_is_insufficient() -> None:
    draft = AnswerDraft(
        answer_markdown="The indexed code doesn't mention payments.",
        claims=[],
        refused=True,
        refusal_reason="No payment code in the evidence.",
    )
    result = await system(FakeLLM([draft])).answer(1, "How are payments made?", Budget())
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert result.refusal_reason == "No payment code in the evidence."
    assert result.answer_markdown == draft.answer_markdown
    assert result.draft == draft
    assert result.cost_usd > 0


async def test_no_surviving_claim_is_refused_and_the_draft_kept() -> None:
    draft = AnswerDraft(
        answer_markdown="It uses Django [c1].",
        claims=[Claim(id="c1", text="It uses Django.", evidence_ids=["E1"], symbol="django")],
        refused=False,
    )
    result = await system(FakeLLM([draft])).answer(1, QUESTION, Budget())
    assert (result.refused, result.refusal_kind) == (True, "evidence")
    assert result.answer_markdown == UNVERIFIED_TEXT
    assert result.claims == []
    assert result.draft == draft
    assert result.metrics.dropped_claim_rate == 1.0
    assert result.metrics.prefilter_citation_validity == 0.0


async def test_errors_end_the_trace_as_an_error() -> None:
    sink = Sink()
    with pytest.raises(RuntimeError):
        await system(FakeLLM([RuntimeError("down")]), sink=sink).answer(1, QUESTION, Budget())
    assert sink.traces[0].status == "error"


def test_the_registry() -> None:
    assert answer_systems["oneshot"] is OneShotAnswerSystem
    assert OneShotAnswerSystem.name == "oneshot"
