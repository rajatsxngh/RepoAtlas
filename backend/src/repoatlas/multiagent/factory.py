"""The multi-agent answer system the API, the CLI, and the eval runner use, built from
settings."""

from __future__ import annotations

from sqlalchemy import Engine

from repoatlas.agent.factory import toolbox_factory
from repoatlas.answers.policy import RefusalPolicy
from repoatlas.answers.schema import Budget
from repoatlas.answers.validate import CitationValidator, StoreBlobs
from repoatlas.config import Settings
from repoatlas.limits import SpendGuard
from repoatlas.llm.client import LLM, LLMClient
from repoatlas.llm.pricing import PriceTable
from repoatlas.multiagent.models import MultiAgentConfig
from repoatlas.multiagent.system import MultiAgentAnswerSystem
from repoatlas.store.graphstore import GraphStore
from repoatlas.tracing import TraceSink
from repoatlas.tracing.store import SqlTraceSink


def multiagent_limits(settings: Settings) -> MultiAgentConfig:
    """The pipeline's limits. A branch is an agent exploration, so the agent's per-branch
    settings (steps of one exploration, size of a tool result) apply to it."""
    return MultiAgentConfig(
        max_branches=settings.multiagent_max_branches,
        concurrency=settings.multiagent_concurrency,
        total_steps=settings.multiagent_total_steps,
        branch_max_steps=max(2, settings.agent_max_steps),
        max_input_tokens=settings.multiagent_max_input_tokens,
        per_tool_result_tokens=settings.agent_per_tool_result_tokens,
        branch_timeout_s=settings.multiagent_branch_timeout_s,
        evidence_tokens=settings.answer_evidence_tokens,
    )


def multiagent_budget(settings: Settings) -> Budget:
    """The cost and output limits of one multi-agent answer."""
    return Budget(
        max_cost_usd=settings.multiagent_max_cost_usd,
        max_output_tokens=settings.answer_max_output_tokens,
    )


def multiagent_system(
    settings: Settings,
    engine: Engine,
    llm: LLM,
    store: GraphStore | None = None,
    *,
    sink: TraceSink | None = None,
) -> MultiAgentAnswerSystem:
    """The pipeline over the database, with ``llm`` as its model."""
    store = store or GraphStore.from_settings(settings, engine)
    blobs = StoreBlobs(engine)
    return MultiAgentAnswerSystem(
        llm,
        toolbox_factory(settings, store),
        CitationValidator(blobs),
        RefusalPolicy.from_settings(settings),
        blobs=blobs,
        prices=PriceTable(settings.llm_prices),
        sink=sink or SqlTraceSink(engine),
        limits=multiagent_limits(settings),
        efforts={"small": settings.llm_small_effort, "large": settings.llm_large_effort},
        default_budget=multiagent_budget(settings),
    )


def multiagent_from_settings(
    settings: Settings, engine: Engine, store: GraphStore | None = None
) -> MultiAgentAnswerSystem:
    """Raises ``LLMNotConfigured`` without an API key. Calls count against the settings' daily
    spend cap (``SpendGuard``)."""
    llm = LLMClient.from_settings(settings, guard=SpendGuard.from_settings(settings, engine))
    return multiagent_system(settings, engine, llm, store)
