"""The smoke run on py_small, offline: summary numbers, the cost cap, stored runs, spans."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import Budget
from repoatlas.answers.smoke import SmokeQuestion, SmokeSet, run_smoke, summary_lines
from repoatlas.answers.system import OneShotAnswerSystem
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.cli import app
from repoatlas.config import get_settings
from repoatlas.llm.client import LLMClient
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from repoatlas.tracing.store import SqlTraceSink
from tests.integration.answers.recorded import DraftFromEvidence
from tests.integration.retrieval.conftest import store_fixture

pytestmark = pytest.mark.anyio
TOTAL = "py:src/shop/cart.py:Cart.total:method"
QUESTIONS = SmokeSet(
    repo="acme/py_small",
    commit="a" * 40,
    questions=[
        SmokeQuestion(
            id="a1",
            question="How does Cart.total compute the total?",
            answerable=True,
            expected_symbols=[TOTAL],
        ),
        SmokeQuestion(id="u1", question="How are invoices emailed to customers?", answerable=False),
        SmokeQuestion(id="a2", question="How does Cart.add add an item?", answerable=True),
    ],
)


def system(engine: Engine) -> OneShotAnswerSystem:
    llm = LLMClient(
        DraftFromEvidence(),
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        prices=PriceTable(DEFAULT_PRICES),
    )
    return OneShotAnswerSystem(
        Retriever(GraphStore(engine), None),
        llm,
        CitationValidator(StoreBlobs(engine)),
        RefusalPolicy(),
        prices=PriceTable(DEFAULT_PRICES),
        sink=SqlTraceSink(engine),
    )


async def test_a_smoke_run_summarizes_and_stores_every_answer(
    pg_engine: Engine, clean_store: None
) -> None:
    snapshot_id = store_fixture(pg_engine, "py_small")
    seen: list[str] = []
    summary = await run_smoke(
        system(pg_engine),
        QUESTIONS,
        snapshot_id=snapshot_id,
        engine=pg_engine,
        max_cost_usd=1.0,
        budget=Budget(),
        worst_case_usd=0.1,
        progress=lambda item: seen.append(item.id),
    )
    assert seen == ["a1", "u1", "a2"]
    a1, u1, a2 = summary.items
    assert (a1.status, a1.expected_cited, a1.prefilter_citation_validity) == ("answered", True, 1.0)
    assert u1.status == "refused" and u1.refusal_kind == "evidence"
    assert a2.status in ("answered", "refused")  # 'def total' isn't in add's evidence
    assert summary.unanswerable_refused_for_evidence == summary.unanswerable_total == 1
    assert summary.every_answer_has_a_trace
    assert summary.every_llm_call_has_a_span
    assert a1.spans is not None and a1.spans.llm_spans == 1
    assert summary.total_cost_usd == pytest.approx(sum(item.cost_usd for item in summary.items))
    with pg_engine.connect() as connection:
        runs = connection.execute(text("SELECT count(*) FROM qa_runs")).scalar_one()
    assert runs == 3
    lines = "\n".join(summary_lines(summary))
    assert "Unanswerable refused for evidence: 1 of 1" in lines


async def test_the_cost_cap_skips_what_could_exceed_it(
    pg_engine: Engine, clean_store: None
) -> None:
    snapshot_id = store_fixture(pg_engine, "py_small")
    summary = await run_smoke(
        system(pg_engine),
        QUESTIONS,
        snapshot_id=snapshot_id,
        engine=pg_engine,
        max_cost_usd=0.045,
        budget=Budget(),
        worst_case_usd=0.042,  # each scripted answer costs $0.005
    )
    assert [item.status for item in summary.items] == ["answered", "skipped", "skipped"]
    assert summary.skipped == 2
    assert summary.total_cost_usd <= 0.045


def test_the_cli_refuses_a_snapshot_of_another_repo(
    pg_url: str, pg_engine: Engine, clean_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id = store_fixture(pg_engine, "py_small")
    monkeypatch.setenv("REPOATLAS_DATABASE_URL", pg_url)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(
            app, ["ask-smoke", "--snapshot", str(snapshot_id), "--max-cost", "1"]
        )
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 2
    assert "colinhacks/zod" in result.output
