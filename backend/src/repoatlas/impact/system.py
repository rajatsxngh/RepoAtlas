"""The registered impact system ``impact_engine`` (IMP-9): ``ImpactEngine`` behind the
evaluation contract (``ImpactSystem``, ``ImpactPrediction``). Also ``impact_explain``, the cited
explanation of an impact report as an answer system (``ImpactExplainSystem``).

The adapter keeps the report's ranking. Ground truth names symbols (definitions and tests), so
items that are not symbols (files, directories, routes) are left out of the prediction. Unknown
sites stay out of it by default (``include_unknown``): they are possibilities, not predictions;
the evaluation measures them separately. With ``include_unknown``, each unknown site's
enclosing symbol is added once, in the ``unknown`` group, with score 0.
"""

from __future__ import annotations

import hashlib
from typing import Any

import anyio

from repoatlas.answers.prompting import SYSTEM_PROMPT
from repoatlas.answers.schema import AnswerResult, Budget
from repoatlas.answers.system import StatusCallback
from repoatlas.evals.registry import (
    EvalContext,
    ImpactPrediction,
    ImpactTarget,
    register_answer_system,
    register_impact_system,
)
from repoatlas.evals.registry import (
    ImpactItem as PredictedItem,
)
from repoatlas.impact.engine import ImpactEngine
from repoatlas.impact.explain import ImpactExplainer
from repoatlas.impact.schema import DiffTarget, ImpactParams, ImpactReport, SymbolTarget
from repoatlas.impact.schema import ImpactItem as ReportItem
from repoatlas.impact.store_data import StoreImpactData
from repoatlas.parsing.symbols import parse_symbol_key

IMPACT_SYSTEM_VERSION = 1  # bump when the engine's output changes for the same parameters


def is_symbol_key(key: str) -> bool:
    try:
        parse_symbol_key(key)
    except ValueError:
        return False
    return True


def path_text(report_item: ReportItem) -> str:
    steps = report_item.best_path
    if not steps:
        return report_item.symbol_key
    parts = [steps[0].src]
    for step in steps:
        parts.append(f"-{step.kind}-> {step.dst}")
    return " ".join(parts)


def to_prediction(
    report: ImpactReport, target: ImpactTarget, *, include_unknown: bool = False
) -> ImpactPrediction:
    items = [
        PredictedItem(
            symbol_key=item.symbol_key,
            group=item.group,
            score=item.score,
            path=path_text(item),
            evidence=item.evidence or None,
        )
        for item in report.items
        if is_symbol_key(item.symbol_key)
    ]
    if include_unknown:
        seen = {item.symbol_key for item in items} | set(report.targets)
        for site in report.unknown:
            key = site.site.symbol_key
            if key is None or key in seen or not is_symbol_key(key):
                continue
            seen.add(key)
            items.append(
                PredictedItem(symbol_key=key, group="unknown", score=0.0, evidence=[site.site])
            )
    return ImpactPrediction(target=target, items=items)


class EngineImpactSystem:
    name = "impact_engine"

    def __init__(
        self,
        engine: ImpactEngine,
        params: ImpactParams | None = None,
        *,
        include_unknown: bool = False,
    ) -> None:
        self.engine = engine
        self.params = params or ImpactParams()
        self.include_unknown = include_unknown
        self.last_report: ImpactReport | None = None

    def config(self) -> dict[str, Any]:
        return {
            "version": IMPACT_SYSTEM_VERSION,
            "params": self.params.model_dump(mode="json"),
            "include_unknown": self.include_unknown,
        }

    def report(self, snapshot_id: int, target: ImpactTarget) -> ImpactReport:
        engine_target: SymbolTarget | DiffTarget
        if target.symbol_key is not None:
            engine_target = SymbolTarget(symbol_key=target.symbol_key)
        else:
            assert target.diff is not None
            engine_target = DiffTarget(diff=target.diff)
        return self.engine.analyze(snapshot_id, engine_target, self.params)

    async def predict(self, snapshot_id: int, target: ImpactTarget) -> ImpactPrediction:
        report = await anyio.to_thread.run_sync(self.report, snapshot_id, target)
        return to_prediction(report, target, include_unknown=self.include_unknown)


def impact_engine(ctx: EvalContext) -> EngineImpactSystem:
    """``ctx.options``: ``params`` (``ImpactParams`` fields) and ``include_unknown``."""
    if ctx.store is None:
        from repoatlas.store.graphstore import GraphStore

        ctx.store = GraphStore.from_settings(ctx.settings, ctx.engine)
    params = ImpactParams.model_validate(ctx.options.get("params") or {})
    return EngineImpactSystem(
        ImpactEngine(StoreImpactData(ctx.store)),
        params,
        include_unknown=bool(ctx.options.get("include_unknown", False)),
    )


register_impact_system("impact_engine", impact_engine, replace=True)


class ImpactExplainSystem:
    """Impact explanations as an answer system (GRD-6): the "question" is the key of the symbol
    that changes; the answer is the cited explanation of its impact report. Registered so the
    injection suite (and any other suite) can run explanations like any answering mode."""

    name = "impact_explain"

    def __init__(
        self,
        engine: ImpactEngine,
        explainer: ImpactExplainer,
        params: ImpactParams | None = None,
        *,
        effort: str | None = None,
    ) -> None:
        self.engine = engine
        self.explainer = explainer
        self.params = params or ImpactParams()
        self.effort = effort

    def config(self) -> dict[str, Any]:
        return {
            "kind": "impact_explain",
            "version": IMPACT_SYSTEM_VERSION,
            "params": self.params.model_dump(mode="json"),
            "per_group": self.explainer.per_group,
            "model": self.explainer.llm.model_for("large"),
            "effort": self.effort,
            "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        }

    async def answer(
        self,
        snapshot_id: int,
        question: str,
        budget: Budget,
        *,
        on_status: StatusCallback | None = None,
    ) -> AnswerResult:
        key = question.strip().strip("`")
        if not is_symbol_key(key):
            raise ValueError("impact_explain takes the changed symbol's key as its question.")
        if on_status is not None:
            await on_status("retrieving")
        report = await anyio.to_thread.run_sync(
            self.engine.analyze, snapshot_id, SymbolTarget(symbol_key=key), self.params
        )
        if on_status is not None:
            await on_status("answering")
        return await self.explainer.explain(snapshot_id, report, budget)


def impact_explain(ctx: EvalContext) -> ImpactExplainSystem:
    """``ctx.options``: ``params`` (``ImpactParams`` fields)."""
    from repoatlas.answers.validate import StoreBlobs
    from repoatlas.llm.pricing import PriceTable
    from repoatlas.tracing.store import SqlTraceSink

    if ctx.llm is None:
        raise ValueError("This system needs an LLM client.")
    if ctx.engine is None:
        raise ValueError("This system needs the database.")
    if ctx.store is None:
        from repoatlas.store.graphstore import GraphStore

        ctx.store = GraphStore.from_settings(ctx.settings, ctx.engine)
    data = StoreImpactData(ctx.store)
    explainer = ImpactExplainer(
        data,
        StoreBlobs(ctx.engine),
        ctx.llm,
        prices=PriceTable(ctx.settings.llm_prices),
        sink=SqlTraceSink(ctx.engine),
    )
    return ImpactExplainSystem(
        ImpactEngine(data),
        explainer,
        ImpactParams.model_validate(ctx.options.get("params") or {}),
        effort=ctx.settings.llm_large_effort,
    )


register_answer_system("impact_explain", impact_explain, replace=True)
