"""Synthetic questions, symbols, and systems for evaluation tests."""

from __future__ import annotations

from typing import Any

import anyio

from repoatlas.answers.schema import AnswerMetrics, AnswerResult, Citation, Claim
from repoatlas.bench.schema import AnswerSetTruth, Location, LookupTruth, UnanswerableTruth
from repoatlas.evals.mapping import SymbolEntry, SymbolIndex
from repoatlas.evals.registry import EvalItem, Suite
from repoatlas.evals.scorers import score_benchmark
from repoatlas.llm.types import Usage
from repoatlas.retrieval.evidence import EvidenceRef

OID = "b" * 40
COMMIT = "c" * 40


def entry(
    key: str,
    name: str,
    path: str,
    start: int,
    end: int,
    *,
    kind: str = "function",
    parent: str | None = None,
    qualified: str | None = None,
) -> SymbolEntry:
    return SymbolEntry(
        key=key,
        name=name,
        qualified_name=qualified or name,
        kind=kind,
        path=path,
        name_line=start,
        start=start,
        end=end,
        parent=parent,
    )


F = "py:src/m.py:f:function"
G = "py:src/m.py:g:function"
CLS = "py:src/m.py:K:class"
METH = "py:src/m.py:K.run:method"
OTHER_F = "py:src/o.py:f:function"

INDEX = SymbolIndex(
    [
        SymbolEntry(
            key="file:src/m.py",
            name="m.py",
            qualified_name="src/m.py",
            kind="file",
            path="src/m.py",
            name_line=None,
            start=None,
            end=None,
            parent=None,
        ),
        entry(F, "f", "src/m.py", 10, 20),
        entry(G, "g", "src/m.py", 22, 30),
        entry(CLS, "K", "src/m.py", 40, 80, kind="class"),
        entry(METH, "run", "src/m.py", 45, 60, kind="method", parent=CLS, qualified="K.run"),
        entry(OTHER_F, "f", "src/o.py", 1, 5),
    ]
)


def ref(path: str, start: int, end: int, key: str | None = None) -> EvidenceRef:
    return EvidenceRef(blob_oid=OID, path=path, start_line=start, end_line=end, symbol_key=key)


def items() -> list[EvalItem]:
    return [
        EvalItem(
            id="r-lookup-001",
            type="lookup",
            text="Where is `f` defined?",
            repo_id="r",
            commit=COMMIT,
            split="dev",
            truth=LookupTruth(definition=Location(symbol_key=F, path="src/m.py", line=10)),
        ),
        EvalItem(
            id="r-callers-001",
            type="callers",
            text="Which functions call `run` (defined in `src/m.py`)?",
            repo_id="r",
            commit=COMMIT,
            split="dev",
            truth=AnswerSetTruth(answers=[Location(symbol_key=G, path="src/m.py", line=22)]),
        ),
        EvalItem(
            id="r-unanswerable-001",
            type="unanswerable",
            text="What does `zz` do?",
            repo_id="r",
            commit=COMMIT,
            split="dev",
            truth=UnanswerableTruth(),
        ),
    ]


SUITE = Suite(name="t", loader=lambda split: items(), scorers={"*": score_benchmark})


def answer(
    claims: list[tuple[str, str | None, list[EvidenceRef]]],
    *,
    cost: float = 0.01,
    refused: bool = False,
    kind: str | None = None,
) -> AnswerResult:
    citations = [
        Citation(claim_id=cid, evidence_id=f"E{i}", ref=r)
        for cid, _, refs in claims
        for i, r in enumerate(refs, start=1)
    ]
    return AnswerResult(
        answer_markdown="answer",
        claims=[
            Claim(id=cid, text=f"claim {cid}", evidence_ids=[], symbol=sym)
            for cid, sym, _ in claims
        ],
        citations=citations,
        refused=refused,
        refusal_kind=kind,  # type: ignore[arg-type]
        cost_usd=cost,
        usage=Usage(input_tokens=100, output_tokens=10),
        metrics=AnswerMetrics(
            prefilter_citation_validity=1.0 if citations else None,
            citations_total=len(citations),
            citations_valid=len(citations),
        ),
    )


class ScriptedSystem:
    """Answers by question text; counts calls and the most calls in flight at once."""

    name = "scripted"

    def __init__(self, script: dict[str, Any], *, delay: float = 0.0) -> None:
        self.script = script
        self.delay = delay
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    def config(self) -> dict[str, Any]:
        return {"mode": "test"}

    async def answer(self, snapshot_id, question, budget, *, on_status=None) -> AnswerResult:
        self.calls.append(question)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await anyio.sleep(self.delay)
            value = self.script[question]
            if isinstance(value, Exception):
                raise value
            return value  # type: ignore[no-any-return]
        finally:
            self.active -= 1
