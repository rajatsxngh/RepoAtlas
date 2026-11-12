"""The four baselines with ``FakeLLM``: same model, prompt, schema, and budget; their modes."""

from __future__ import annotations

from typing import Any

import pytest

from repoatlas.answers.prompting import SYSTEM_PROMPT
from repoatlas.answers.schema import AnswerDraft, Budget, Claim
from repoatlas.answers.validate import CitationValidator
from repoatlas.config import Settings
from repoatlas.evals import registry
from repoatlas.evals.baselines import (
    BASELINES,
    CLOSED_BOOK_PROMPT,
    NO_THRESHOLD,
    closed_book,
    retrieval_baseline,
)
from repoatlas.evals.mapping import answer_output
from repoatlas.evals.registry import EvalContext
from repoatlas.evals.runner import system_fingerprint
from repoatlas.llm.fake import FakeLLM, refusal
from repoatlas.retrieval.retriever import EvidenceItem
from tests.unit.answers.helpers import EVIDENCE, FakeBlobs
from tests.unit.answers.test_system import FakeRetriever, Sink, strong
from tests.unit.evals.helpers import INDEX, F

pytestmark = pytest.mark.anyio
QUESTION = "How is the app created?"
DRAFT = AnswerDraft(
    answer_markdown="`create_app` builds an `App` [c1].",
    claims=[
        Claim(id="c1", text="create_app builds an App.", evidence_ids=["E1"], symbol="create_app")
    ],
    refused=False,
)


def context(llm: FakeLLM, **options: Any) -> EvalContext:
    return EvalContext(settings=Settings(), llm=llm, options=options)


def build(name: str, llm: FakeLLM, evidence: list[EvidenceItem] | None = None):
    mode = BASELINES[name]
    assert mode is not None
    retriever = FakeRetriever(strong(EVIDENCE) if evidence is None else evidence)
    system = retrieval_baseline(
        name,
        mode,
        context(llm),
        retriever=retriever,
        validator=CitationValidator(FakeBlobs()),
        sink=Sink(),
    )
    return system, retriever


def test_all_four_baselines_are_registered() -> None:
    names = registry.answer_system_names()
    for name in ("closed_book", "bm25_only", "vector_only", "hybrid_graphrag"):
        assert name in names
        assert registry.system_kind(name) == "answer"


@pytest.mark.parametrize(
    ("name", "mode"),
    [("bm25_only", "keyword"), ("vector_only", "vector"), ("hybrid_graphrag", "hybrid")],
)
async def test_retrieval_baselines_differ_only_in_retrieval(name: str, mode: str) -> None:
    llm = FakeLLM([DRAFT])
    system, retriever = build(name, llm)
    result = await system.answer(3, QUESTION, Budget())
    assert retriever.calls == [(3, QUESTION, mode)]
    assert not result.refused and len(result.citations) == 1
    call = llm.calls[0]
    assert call.role == "large" and call.schema is AnswerDraft
    assert call.messages[0]["content"] == SYSTEM_PROMPT
    config = system.config()
    assert config["mode"] == mode
    assert config["model"] == "claude-sonnet-5-5"
    assert config["evidence_tokens"] == Settings().answer_evidence_tokens


async def test_only_hybrid_uses_the_fusion_score_threshold() -> None:
    weak = [EvidenceItem.model_validate({**i.model_dump(), "score": 0.001}) for i in EVIDENCE]
    hybrid, _ = build("hybrid_graphrag", FakeLLM([]), weak)
    refused = await hybrid.answer(3, QUESTION, Budget())
    assert refused.refused and refused.refusal_kind == "evidence"
    vector, _ = build("vector_only", FakeLLM([DRAFT]), weak)
    assert vector.policy.min_retrieval_score == NO_THRESHOLD
    assert not (await vector.answer(3, QUESTION, Budget())).refused
    empty, _ = build("bm25_only", FakeLLM([]), [])
    nothing = await empty.answer(3, QUESTION, Budget())
    assert nothing.refused and nothing.refusal_kind == "evidence"


async def test_baseline_fingerprints_differ() -> None:
    prints = set()
    for name in ("bm25_only", "vector_only", "hybrid_graphrag"):
        system, _ = build(name, FakeLLM([]))
        prints.add(system_fingerprint(name, system.config(), {}, Budget()))
    book = closed_book(context(FakeLLM([]), repo_for=lambda sid: ("pallets/click", "c" * 40)))
    prints.add(system_fingerprint("closed_book", book.config(), {}, Budget()))
    assert len(prints) == 4


async def test_closed_book_names_the_repo_and_cites_nothing() -> None:
    draft = AnswerDraft(
        answer_markdown="`f` is defined in src/m.py [c1].",
        claims=[Claim(id="c1", text="f is defined in src/m.py.", evidence_ids=[], symbol="f")],
        refused=False,
    )
    llm = FakeLLM([draft])
    system = closed_book(context(llm, repo_for=lambda sid: ("acme/tool", "c" * 40)), sink=Sink())
    result = await system.answer(9, "Where is `f` defined?", Budget())
    messages = llm.calls[0].messages
    assert messages[0]["content"] == CLOSED_BOOK_PROMPT
    assert "acme/tool at commit " + "c" * 40 in messages[1]["content"][0]["text"]
    assert "Where is `f` defined?" in messages[1]["content"][1]["text"]
    assert llm.calls[0].schema is AnswerDraft and llm.calls[0].role == "large"
    assert not result.refused and result.citations == [] and len(result.claims) == 1


async def test_closed_book_resolves_names_by_mentioned_path() -> None:
    draft = AnswerDraft(
        answer_markdown="`f` is in src/m.py [c1].",
        claims=[Claim(id="c1", text="f is defined in src/m.py.", evidence_ids=[], symbol="f")],
        refused=False,
    )
    system = closed_book(
        context(FakeLLM([draft]), repo_for=lambda sid: ("a/b", "c" * 40)), sink=Sink()
    )
    output = answer_output(await system.answer(1, "?", Budget()), INDEX, 0.1)
    assert output.answer_keys == [F]
    assert output.outcome == "answered"


async def test_closed_book_refusals_and_budget() -> None:
    declined = AnswerDraft(
        answer_markdown="I don't know.", claims=[], refused=True, refusal_reason="unknown"
    )
    system = closed_book(
        context(FakeLLM([declined]), repo_for=lambda sid: ("a/b", "c" * 40)), sink=Sink()
    )
    result = await system.answer(1, "?", Budget())
    assert result.refused and result.refusal_kind == "evidence"
    api = closed_book(
        context(FakeLLM([refusal()]), repo_for=lambda sid: ("a/b", "c" * 40)), sink=Sink()
    )
    assert (await api.answer(1, "?", Budget())).refusal_kind == "api"
    tiny = closed_book(context(FakeLLM([]), repo_for=lambda sid: ("a/b", "c" * 40)), sink=Sink())
    budget = await tiny.answer(1, "?", Budget(max_cost_usd=1e-9))
    assert budget.refusal_kind == "budget"


def test_factories_need_an_llm() -> None:
    with pytest.raises(ValueError, match="LLM"):
        closed_book(EvalContext(settings=Settings()))
    with pytest.raises(ValueError, match="LLM"):
        retrieval_baseline("vector_only", "vector", EvalContext(settings=Settings()))
