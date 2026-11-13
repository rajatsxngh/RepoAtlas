"""The answer system the API and the CLI use, built from settings."""

from __future__ import annotations

from sqlalchemy import Engine

from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.system import OneShotAnswerSystem
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.config import Settings
from repoatlas.limits import SpendGuard
from repoatlas.llm.client import LLMClient
from repoatlas.llm.pricing import PriceTable
from repoatlas.retrieval.embed import embedder_for
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from repoatlas.tracing.store import SqlTraceSink


def oneshot_from_settings(
    settings: Settings, engine: Engine, store: GraphStore | None = None
) -> OneShotAnswerSystem:
    """Raises ``LLMNotConfigured`` without an API key. Calls count against the settings' daily
    spend cap (``SpendGuard``)."""
    llm = LLMClient.from_settings(settings, guard=SpendGuard.from_settings(settings, engine))
    return OneShotAnswerSystem(
        Retriever(store or GraphStore.from_settings(settings, engine), embedder_for(settings)),
        llm,
        CitationValidator(StoreBlobs(engine)),
        RefusalPolicy.from_settings(settings),
        prices=PriceTable(settings.llm_prices),
        sink=SqlTraceSink(engine),
        evidence_tokens=settings.answer_evidence_tokens,
    )
