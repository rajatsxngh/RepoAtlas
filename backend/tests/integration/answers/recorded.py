"""One answer end to end on the py_small fixture, shared by the replay test and the live
recording (``tests/live/test_answer_live.py``)."""

from __future__ import annotations

import json
import re
from typing import Any

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


class DraftFromEvidence:
    """A transport that answers like the model would, offline: it cites the evidence block
    containing ``needle`` (or refuses when none does), with that block's symbol."""

    def __init__(self, needle: str = "def total", symbol: str = "total") -> None:
        self.needle = needle
        self.symbol = symbol
        self.requests: list[dict[str, Any]] = []

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        prompt: str = request["messages"][0]["content"][0]["text"]
        blocks = re.findall(r'<evidence id="(E\d+)"[^>]*>\n(.*?)\n</evidence>', prompt, re.S)
        hit = next((eid for eid, text in blocks if self.needle in text), None)
        if hit is None:
            draft: dict[str, Any] = {
                "answer_markdown": "The indexed code doesn't show this.",
                "claims": [],
                "refused": True,
                "refusal_reason": "Nothing in the evidence is about it.",
            }
        else:
            draft = {
                "answer_markdown": f"`{self.symbol}` does it [c1].",
                "claims": [
                    {"id": "c1", "text": "It does.", "evidence_ids": [hit], "symbol": self.symbol}
                ],
                "refused": False,
                "refusal_reason": None,
            }
        return {
            "model": request["model"],
            "content": [{"type": "text", "text": json.dumps(draft)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1500, "output_tokens": 200},
        }
