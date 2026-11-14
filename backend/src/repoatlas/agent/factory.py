"""The agent answer system the API, the CLI, and the eval runner use, built from settings."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine

from repoatlas.agent.loop import AgentAnswerSystem, AgentConfig
from repoatlas.agent.tools import Toolbox
from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import Budget
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.config import Settings
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.store_data import StoreImpactData
from repoatlas.limits import SpendGuard
from repoatlas.llm.client import LLM, LLMClient
from repoatlas.llm.pricing import PriceTable
from repoatlas.retrieval.embed import embedder_for
from repoatlas.retrieval.retriever import Retriever
from repoatlas.store.graphstore import GraphStore
from repoatlas.tracing import TraceSink
from repoatlas.tracing.store import SqlTraceSink


def agent_limits(settings: Settings) -> AgentConfig:
    return AgentConfig(
        max_steps=settings.agent_max_steps,
        max_input_tokens=settings.agent_max_input_tokens,
        per_tool_result_tokens=settings.agent_per_tool_result_tokens,
    )


def agent_budget(settings: Settings) -> Budget:
    """The cost and output limits of one agent answer."""
    return Budget(
        max_cost_usd=settings.agent_max_cost_usd,
        max_output_tokens=settings.answer_max_output_tokens,
    )


def toolbox_factory(settings: Settings, store: GraphStore) -> Callable[[int], Toolbox]:
    """Toolboxes over ``store``, one per snapshot asked for. Every tool reads through the
    store, the retriever, or the impact engine: nothing here calls a model."""
    retriever = Retriever(store, embedder_for(settings))
    impact = ImpactEngine(StoreImpactData(store))

    def toolbox_for(snapshot_id: int) -> Toolbox:
        return Toolbox(snapshot_id, store, retriever, impact)

    return toolbox_for


def agent_system(
    settings: Settings,
    engine: Engine,
    llm: LLM,
    store: GraphStore | None = None,
    *,
    sink: TraceSink | None = None,
) -> AgentAnswerSystem:
    """An agent over the database, with ``llm`` as its model."""
    store = store or GraphStore.from_settings(settings, engine)
    blobs = StoreBlobs(engine)
    return AgentAnswerSystem(
        llm,
        toolbox_factory(settings, store),
        CitationValidator(blobs),
        RefusalPolicy.from_settings(settings),
        blobs=blobs,
        prices=PriceTable(settings.llm_prices),
        sink=sink or SqlTraceSink(engine),
        limits=agent_limits(settings),
        efforts={"small": settings.llm_small_effort, "large": settings.llm_large_effort},
    )


def agent_from_settings(
    settings: Settings, engine: Engine, store: GraphStore | None = None
) -> AgentAnswerSystem:
    """Raises ``LLMNotConfigured`` without an API key. Calls count against the settings' daily
    spend cap (``SpendGuard``)."""
    llm = LLMClient.from_settings(settings, guard=SpendGuard.from_settings(settings, engine))
    return agent_system(settings, engine, llm, store)
