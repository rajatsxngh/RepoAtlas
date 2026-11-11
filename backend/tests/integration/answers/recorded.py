"""One answer end to end on the py_small fixture, shared by the replay test and the live
recording (``tests/live/test_answer_live.py``)."""

from __future__ import annotations

from sqlalchemy import Engine

from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.system import OneShotAnswerSystem
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.llm.cassette import CassetteTransport
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from tests.cassettes import CASSETTES, cassette_client

ANSWER_CASSETTE = CASSETTES / "answers" / "py_small_cart_total.json"
QUESTION = "How does Cart.total compute the cart's total?"


async def answer_with(
    transport: CassetteTransport, engine: Engine, snapshot_id: int
) -> AnswerResult:
    """Keyword-only retrieval (the stored fixture has no embeddings), so the prompt is the
    same on every machine."""
    system = OneShotAnswerSystem(
        Retriever(GraphStore(engine), None),
        cassette_client(transport),
        CitationValidator(StoreBlobs(engine)),
        RefusalPolicy(),
        prices=PriceTable(DEFAULT_PRICES),
    )
    return await system.answer(snapshot_id, QUESTION, Budget())
