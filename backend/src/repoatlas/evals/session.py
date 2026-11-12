"""Putting an evaluation run together from settings: the system, snapshots, cache, enrichers.

Kept apart from the CLI so it can be tested with fakes (``prepare_run`` takes an optional
``context``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repoatlas.evals.registry import (
    EvalContext,
    EvalItem,
    get_answer_system,
    get_impact_system,
    get_suite,
    system_kind,
)
from repoatlas.evals.report import ReportMeta, SnapshotUsed
from repoatlas.evals.runner import (
    Enricher,
    EvalRun,
    ResultCache,
    system_config,
    system_fingerprint,
)


@dataclass
class EvalOptions:
    system: str
    suite: str = "benchmark"
    split: str = "dev"
    runs: int = 1
    max_cost_usd: float | None = 1.0
    concurrency: int = 4
    resume: bool = False
    snapshots: list[str] = field(default_factory=list)
    support: bool = True
    judge: bool = True
    only: list[str] = field(default_factory=list)
    cache_dir: Path = Path("eval/cache")


@dataclass
class Prepared:
    run: EvalRun
    context: EvalContext
    snapshots: dict[str, SnapshotUsed]
    suite_info: dict[str, Any]
    enrichers: list[Enricher]


EnricherFactory = Callable[[EvalContext], Enricher]
ENRICHERS: dict[str, EnricherFactory] = {}  # "support" and "judge" register here


def default_context() -> EvalContext:
    from repoatlas.config import get_settings
    from repoatlas.db.engine import get_engine
    from repoatlas.store.graphstore import GraphStore

    settings = get_settings()
    engine = get_engine()
    return EvalContext(
        settings=settings, engine=engine, store=GraphStore.from_settings(settings, engine)
    )


def _llm(context: EvalContext) -> None:
    if context.llm is None:
        from repoatlas.llm.client import LLMClient

        context.llm = LLMClient.from_settings(context.settings)


def prepare_run(
    options: EvalOptions,
    *,
    context: EvalContext | None = None,
    progress: Callable[[Any], None] | None = None,
) -> Prepared:
    from repoatlas.evals.mapping import GraphIndexes
    from repoatlas.evals.snapshots import parse_overrides, resolve_snapshots
    from repoatlas.pipeline.stages import config_fingerprint

    suite = get_suite(options.suite)
    items: list[EvalItem] = suite.load(options.split)
    if options.only:
        wanted = set(options.only)
        items = [item for item in items if item.id in wanted]
        unknown = wanted - {item.id for item in items}
        if unknown:
            raise LookupError(
                f"Not in {options.suite}/{options.split}: {', '.join(sorted(unknown))}"
            )
    ctx = context or default_context()
    if ctx.engine is None:
        raise ValueError("An evaluation run needs the database.")
    kind = system_kind(options.system)
    _llm(ctx)
    factory = (
        get_answer_system(options.system) if kind == "answer" else get_impact_system(options.system)
    )
    system = factory(ctx)
    repos = {item.repo_id: item.commit for item in items}
    snapshots = resolve_snapshots(
        ctx.engine,
        repos,
        fingerprint=config_fingerprint(ctx.settings),
        overrides=parse_overrides(options.snapshots),
    )
    from repoatlas.answers.schema import Budget

    budget = Budget(
        max_cost_usd=ctx.settings.answer_max_cost_usd,
        max_output_tokens=ctx.settings.answer_max_output_tokens,
    )
    config = system_config(system)
    fingerprint = system_fingerprint(
        options.system,
        config,
        {repo: used.config_fingerprint for repo, used in snapshots.items()},
        budget,
    )
    import repoatlas.evals.support  # noqa: F401  (registers its enricher)

    enrichers = []
    if kind == "answer":
        for name, enabled in (("support", options.support), ("judge", options.judge)):
            if enabled and name in ENRICHERS:
                enrichers.append(ENRICHERS[name](ctx))
    by_repo = {repo: used.snapshot_id for repo, used in snapshots.items()}
    run = EvalRun(
        system_name=options.system,
        system=system,
        kind=kind,
        suite=suite,
        split=options.split,
        items=items,
        runs=options.runs,
        snapshot_for=lambda item: by_repo.get(item.repo_id),
        index_for=GraphIndexes(ctx.engine),
        cache=ResultCache(options.cache_dir),
        fingerprint=fingerprint,
        config=config,
        budget=budget,
        max_cost_usd=options.max_cost_usd,
        concurrency=options.concurrency,
        resume=options.resume,
        enrichers=enrichers,
        progress=progress,
    )
    return Prepared(
        run=run,
        context=ctx,
        snapshots=snapshots,
        suite_info=suite.info() if suite.info else {},
        enrichers=enrichers,
    )


JUDGE_BIAS_NOTE = (
    "The claim-support checker and the flow judge use the same model family as the answering "
    "systems (a known bias); the judge is blind to system names, sees answers truncated to a "
    "fixed length, and its agreement with human labels is reported separately."
)


def report_meta(prepared: Prepared) -> ReportMeta:
    ctx = prepared.context
    models: dict[str, str] = {}
    llm = ctx.llm
    if llm is not None:
        models = {"small": llm.model_for("small"), "large": llm.model_for("large")}
    notes = []
    if prepared.enrichers:
        notes.append(JUDGE_BIAS_NOTE)
    return ReportMeta(
        suite_info=prepared.suite_info,
        models=models,
        embedding_model=ctx.settings.embedding_model,
        snapshots=sorted(prepared.snapshots.values(), key=lambda s: s.repo_id),
        evaluators={e.name: e.fingerprint for e in prepared.enrichers},
        notes=notes,
    )
