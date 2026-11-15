"""One agent answer end to end on the py_small fixture, shared by the replay test and the live
recording (``tests/live/test_agent_live.py``).

The cassette (``tests/llm_cassettes/agent/py_small_cart_total.json``) holds the four requests
of one agent conversation and their responses: three exploration turns on the ``small`` model
and the final structured answer on the ``large`` model. Replaying it checks, byte for byte,
what the agent sends: the tool definitions, the tool results as they are appended, thinking
blocks passed back unchanged, and the final call's schema and tool choice.

Until the live pass, the cassette is hand-written (``"source": "docs"``, decision 0022): its
responses come from ``DocsModel``, which answers in the documented Messages API shapes. To
regenerate it after a deliberate change to the agent's requests:

    REPOATLAS_WRITE_DOCS_CASSETTES=1 uv run pytest tests/integration/agent/test_recorded_agent.py

The live recording replaces it (``uv run poe record-cassettes``).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from sqlalchemy import Engine

from repoatlas.agent.loop import AgentAnswerSystem
from repoatlas.agent.tools import Toolbox
from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.store_data import StoreImpactData
from repoatlas.llm.cassette import CassetteTransport
from repoatlas.llm.pricing import DEFAULT_PRICES, PriceTable
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from tests.cassettes import CASSETTES, cassette_client

AGENT_CASSETTE = CASSETTES / "agent" / "py_small_cart_total.json"
QUESTION = "How does Cart.total compute the cart's total, and what calls it?"
WRITE_ENV = "REPOATLAS_WRITE_DOCS_CASSETTES"
DOCS_NOTE = (
    "Hand-written (decision 0022): the requests are what the agent sends; the responses were "
    "written from the documented Messages API shapes, not returned by a model. Replace it "
    "with a live recording (`uv run poe record-cassettes`) when spending is allowed."
)
TOTAL = "py:src/shop/cart.py:Cart.total:method"


def writing_docs() -> bool:
    return os.environ.get(WRITE_ENV) == "1"


async def agent_answer_with(
    transport: CassetteTransport, engine: Engine, snapshot_id: int
) -> AnswerResult:
    """The agent over the stored fixture. Retrieval is keyword-only (the stored fixture has no
    embeddings), so every request is the same on every machine."""
    store = GraphStore(engine)
    retriever = Retriever(store, None)
    impact = ImpactEngine(StoreImpactData(store))
    blobs = StoreBlobs(engine)
    system = AgentAnswerSystem(
        cassette_client(transport),
        lambda snapshot: Toolbox(snapshot, store, retriever, impact),
        CitationValidator(blobs),
        RefusalPolicy(),
        blobs=blobs,
        prices=PriceTable(DEFAULT_PRICES),
    )
    return await system.answer(snapshot_id, QUESTION, Budget())


def _message(
    model: str, content: list[dict[str, Any]], stop: str, tokens: tuple[int, int]
) -> dict[str, Any]:
    return {
        "id": f"msg_docs_{tokens[0]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


class DocsModel:
    """A transport that answers one agent conversation in the documented response shapes: a
    thinking block (empty text, a signature) before the first tool call, text and two tool
    calls in one turn, the ``final_answer`` call, and the structured answer as JSON text."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def send(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        self.requests.append(request)
        model = request["model"]
        turns = sum(1 for message in request["messages"] if message["role"] == "assistant")
        if "format" in request.get("output_config", {}):
            return _message(
                model, [{"type": "text", "text": self._draft(request)}], "end_turn", (4210, 180)
            )
        content: list[dict[str, Any]]
        if turns == 0:
            content = [
                {"type": "thinking", "thinking": "", "signature": "docs-signature-0001"},
                {
                    "type": "tool_use",
                    "id": "toolu_docs_01",
                    "name": "find_definition",
                    "input": {"name": "Cart.total"},
                },
            ]
            return _message(model, content, "tool_use", (2890, 64))
        if turns == 1:
            content = [
                {"type": "text", "text": "I'll read the method and check what calls it."},
                {
                    "type": "tool_use",
                    "id": "toolu_docs_02",
                    "name": "read_file",
                    "input": {"path": "src/shop/cart.py", "start": 17, "end": 19},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_docs_03",
                    "name": "find_callers",
                    "input": {"symbol_key": TOTAL},
                },
            ]
            return _message(model, content, "tool_use", (3240, 118))
        content = [{"type": "tool_use", "id": "toolu_docs_04", "name": "final_answer", "input": {}}]
        return _message(model, content, "tool_use", (3980, 31))

    def _draft(self, request: dict[str, Any]) -> str:
        """Cites the ledger entry for the method's lines, by the ID the tool result gave it."""
        results = "\n".join(
            block["content"]
            for message in request["messages"]
            if message["role"] == "user"
            for block in message["content"]
            if block.get("type") == "tool_result"
        )
        found = re.search(r'<evidence id="(E\d+)" path="src/shop/cart.py" lines="17-19"', results)
        assert found is not None, "the method's lines are not in the ledger"
        return json.dumps(
            {
                "answer_markdown": (
                    "`Cart.total` adds up the price of every item in the cart, starting from "
                    "`Decimal(0)` [c1]."
                ),
                "claims": [
                    {
                        "id": "c1",
                        "text": "Cart.total sums the prices of the cart's items.",
                        "evidence_ids": [found[1]],
                        "symbol": "total",
                    }
                ],
                "refused": False,
                "refusal_reason": None,
            }
        )


def docs_transport() -> CassetteTransport:
    """A recording transport over ``DocsModel`` (for writing the hand-written cassette)."""
    return CassetteTransport(
        AGENT_CASSETTE,
        mode="record",
        inner=DocsModel(),
        description="agent answer on py_small: three exploration turns and the final answer",
    )


def save_docs(transport: CassetteTransport) -> None:
    transport.cassette.source = "docs"
    transport.cassette.note = DOCS_NOTE
    transport.save()
