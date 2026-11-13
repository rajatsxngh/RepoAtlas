"""The daily spend cap: totals from the ``spans`` table, the cache, and budget refusals."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.system import DAILY_CAP_TEXT, OneShotAnswerSystem
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.api.app import API_PREFIX
from repoatlas.limits import SpendCapReached, SpendGuard, start_of_day
from repoatlas.llm.client import LLMClient
from repoatlas.llm.offline import OFFLINE_NOTICE, OfflineTransport
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.retrieval.retriever import Retriever
from repoatlas.tracing.store import SqlTraceSink
from tests.integration.api.test_ask import QUESTION, ask, events
from tests.integration.retrieval.conftest import store_fixture
from tests.unit.api.helpers import client_for

NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.tick = 0.0
        self.moment = NOON

    def monotonic(self) -> float:
        return self.tick

    def now(self) -> datetime:
        return self.moment


@pytest.fixture
def spans(pg_engine: Engine) -> Iterator[Engine]:
    """The test database with no traces before or after the test."""
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE traces CASCADE"))
    yield pg_engine
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE traces CASCADE"))


def spend(engine: Engine, cost: float, at: datetime, kind: str = "llm") -> None:
    """Store one finished span that cost ``cost`` dollars at ``at``."""
    trace_id, span_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO traces (id, kind, started_at, ended_at, status, tokens_in, "
                "tokens_out, cost_usd) VALUES (:id, 'answer', :at, :at, 'ok', 0, 0, :cost)"
            ),
            {"id": trace_id, "at": at, "cost": cost},
        )
        connection.execute(
            text(
                "INSERT INTO spans (id, trace_id, seq, name, kind, status, started_at, "
                "ended_at, cost_usd, attrs) VALUES (:id, :trace, 0, 'step', :kind, 'ok', :at, "
                ":at, :cost, CAST('{}' AS jsonb))"
            ),
            {"id": span_id, "trace": trace_id, "kind": kind, "at": at, "cost": cost},
        )


def guard(engine: Engine, cap: float, clock: Clock) -> SpendGuard:
    return SpendGuard(engine, cap, clock=clock.monotonic, now=clock.now)


def test_the_total_is_todays_llm_spans(spans: Engine) -> None:
    clock = Clock()
    spend(spans, 0.40, NOON - timedelta(hours=1))
    spend(spans, 0.25, start_of_day(NOON))  # midnight itself is today
    spend(spans, 9.00, start_of_day(NOON) - timedelta(seconds=1))  # yesterday
    spend(spans, 7.00, NOON, kind="retrieval")  # not an LLM span
    assert guard(spans, 1.0, clock).spent_today() == pytest.approx(0.65)


def test_the_total_is_cached_for_the_ttl_and_counts_this_process_meanwhile(spans: Engine) -> None:
    clock = Clock()
    cap = guard(spans, 1.0, clock)
    assert cap.spent_today() == 0.0
    spend(spans, 0.30, NOON)  # another process
    cap.record(0.20)  # this one
    assert cap.spent_today() == pytest.approx(0.20)
    clock.tick = 29.9
    assert cap.spent_today() == pytest.approx(0.20)
    clock.tick = 30.0  # refreshed: the stored total replaces what was counted meanwhile
    assert cap.spent_today() == pytest.approx(0.30)


def test_the_cap_stops_calls_and_alerts_once_a_day(spans: Engine) -> None:
    clock = Clock()
    cap = guard(spans, 0.50, clock)
    cap.check()
    cap.record(0.50)
    with capture_logs() as logs:
        for _ in range(3):
            with pytest.raises(SpendCapReached) as raised:
                cap.check()
    assert raised.value.spent_usd == pytest.approx(0.50) and raised.value.cap_usd == 0.50
    alerts = [entry for entry in logs if entry["event"] == "daily spend cap reached"]
    assert len(alerts) == 1 and alerts[0]["log_level"] == "error"
    assert alerts[0]["cap_usd"] == 0.50 and alerts[0]["day"] == "2026-11-14"


def test_a_new_day_starts_from_zero(spans: Engine) -> None:
    clock = Clock()
    spend(spans, 2.0, NOON)
    cap = guard(spans, 1.0, clock)
    with pytest.raises(SpendCapReached):
        cap.check()
    clock.moment = NOON + timedelta(hours=12, seconds=1)  # past midnight, cache still fresh
    cap.check()
    assert cap.spent_today() == 0.0


def test_a_negative_cap_is_refused(spans: Engine) -> None:
    with pytest.raises(ValueError, match="negative"):
        SpendGuard(spans, -1.0)


def install(app: FastAPI, cap_usd: float) -> SpendGuard:
    """The app's answer system on the real client, without a model, under a spend cap."""
    engine: Engine = app.state.engine
    spend_guard = SpendGuard(engine, cap_usd)
    llm = LLMClient(
        OfflineTransport(),
        models={"small": "claude-haiku-5-5", "large": "claude-sonnet-5-5"},
        prices=PriceTable(DEFAULT_PRICES),
        guard=spend_guard,
    )
    app.state.answer_systems["oneshot"] = OneShotAnswerSystem(
        Retriever(app.state.store, None),
        llm,
        CitationValidator(StoreBlobs(engine)),
        RefusalPolicy(min_retrieval_score=0.0),
        prices=PriceTable(DEFAULT_PRICES),
        sink=SqlTraceSink(engine),
    )
    return spend_guard


@pytest.mark.anyio
async def test_over_the_cap_an_ask_is_a_budget_refusal(app: FastAPI, spans: Engine) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, cap_usd=1.0)
    spend(spans, 1.0, datetime.now(UTC))
    response = await ask(app, snapshot_id)
    assert response.status_code == 200
    stream = events(response.text)
    answer = next(data for name, data in stream if name == "answer")
    assert answer["refused"] is True and answer["refusal_kind"] == "budget"
    assert answer["answer_markdown"] == DAILY_CAP_TEXT
    assert "daily cap" in answer["refusal_reason"]
    assert answer["citations"] == [] and answer["cost_usd"] == 0
    assert stream[-1][0] == "done"
    async with client_for(app) as client:  # the refusal is a stored run with no LLM span
        trace = await client.get(f"{API_PREFIX}/traces/{stream[-1][1]['trace_id']}")
    assert "llm" not in {span["kind"] for span in trace.json()["spans"]}


@pytest.mark.anyio
async def test_under_the_cap_the_offline_stand_in_answers_with_citations(
    app: FastAPI, spans: Engine
) -> None:
    snapshot_id = store_fixture(app.state.engine, "py_small")
    install(app, cap_usd=1.0)
    response = await ask(app, snapshot_id, QUESTION)
    stream = events(response.text)
    assert [name for name, _ in stream] == ["status", "status", "status", "answer", "done"]
    answer = stream[3][1]
    assert answer["refused"] is False and answer["cost_usd"] == 0
    assert answer["answer_markdown"].startswith(OFFLINE_NOTICE)
    cited = {citation["evidence_id"] for citation in answer["citations"]}
    assert cited and cited <= {"E1", "E2", "E3"}
