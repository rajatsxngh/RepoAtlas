"""The evaluation runner (EVL-6): answer every question of a suite split, ``runs`` times.

- **Caching.** Every output is stored under ``(system config fingerprint, question ID, run
  index)``; a re-run reads it back and costs nothing. The fingerprint covers the system's name
  and configuration (models, prompt, retrieval mode, budgets) and the config fingerprints of the
  snapshots it answers on, so a rebuilt graph or a changed prompt never reuses old answers.
  Checker and judge results (``Enricher``) are cached separately under their own fingerprints,
  so changing the judge doesn't re-run the answers.
- **Cost cap.** Before a question starts, its expected cost (the most an item has cost so far,
  or the answer budget before anything ran) is reserved; a question whose reservation would take
  spent plus reserved past ``max_cost_usd`` is not started, and neither is anything after it.
  The result is then marked incomplete and lists what was skipped. Spend is the traced cost of
  every LLM call (the system's and the enrichers').
- **Concurrency.** Up to ``concurrency`` questions run at once. Runs go in order (all of run 0,
  then run 1, …), so a capped run keeps whole runs where it can.
- **Resume.** Errors are cached too, so a deterministic failure isn't paid for twice; with
  ``resume`` the errored items are retried. Items skipped by the cap were never cached, so the
  same command (with a higher cap) picks up where the last one stopped.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field

from repoatlas.answers.schema import Budget
from repoatlas.bench.schema import ImpactTruth
from repoatlas.canonical import digest, pretty_json, write_text_lf
from repoatlas.evals.mapping import SymbolIndex, answer_output
from repoatlas.evals.outputs import ItemScore, SystemOutput
from repoatlas.evals.registry import EvalItem, ImpactSystem, ImpactTarget, Suite

CACHE_VERSION = 1


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: str
    type: str
    repo_id: str
    run: int
    snapshot_id: int | None
    output: SystemOutput
    score: ItemScore
    cached: bool = False


class Skipped(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: str
    run: int
    reason: str


class RunResult(BaseModel):
    system: str
    kind: Literal["answer", "impact"]
    suite: str
    split: str
    runs: int
    fingerprint: str
    config: dict[str, Any]
    max_cost_usd: float | None
    spent_usd: float  # new spend in this invocation
    records: list[RunRecord]
    skipped: list[Skipped] = Field(default_factory=list)
    duration_s: float = 0.0

    @property
    def complete(self) -> bool:
        return not self.skipped and not any(r.output.outcome == "error" for r in self.records)

    @property
    def total_cost_usd(self) -> float:
        """What every recorded output cost when it ran (cached ones included)."""
        return sum(r.output.cost_usd + r.output.eval_cost_usd for r in self.records)


class ResultCache:
    """JSON files under ``root/<namespace>/<fingerprint>/<question>/<run>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, namespace: str, fingerprint: str, question_id: str, run: int) -> Path:
        return self.root / namespace / fingerprint[:24] / question_id / f"{run}.json"

    def get(self, namespace: str, fingerprint: str, question_id: str, run: int) -> Any | None:
        import json

        path = self._path(namespace, fingerprint, question_id, run)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("cache_version") != CACHE_VERSION or data.get("fingerprint") != fingerprint:
            return None
        return data.get("value")

    def put(self, namespace: str, fingerprint: str, question_id: str, run: int, value: Any) -> None:
        path = self._path(namespace, fingerprint, question_id, run)
        record = {"cache_version": CACHE_VERSION, "fingerprint": fingerprint, "value": value}
        write_text_lf(path, pretty_json(record))


class CostMeter:
    """Spend and reservations against an optional cap."""

    def __init__(self, cap_usd: float | None) -> None:
        self.cap = cap_usd
        self.spent = 0.0
        self.reserved = 0.0
        self.largest = 0.0  # the most one item has cost
        self.stopped = False

    def reserve(self, estimate: float) -> bool:
        if self.stopped:
            return False
        if self.cap is not None and self.spent + self.reserved + estimate > self.cap:
            self.stopped = True
            return False
        self.reserved += estimate
        return True

    def settle(self, estimate: float, actual: float) -> None:
        self.reserved -= estimate
        self.spent += actual
        self.largest = max(self.largest, actual)


EnrichFn = Callable[[EvalItem, SystemOutput, int | None], Awaitable[SystemOutput]]


@dataclass
class Enricher:
    """A step after the answer that adds to the output (claim support, judge scores).

    ``run`` returns the output with its additions and with ``eval_cost_usd`` raised by what it
    spent. ``store``/``load`` move just its additions in and out of the cache."""

    name: str
    fingerprint: str
    applies: Callable[[EvalItem, SystemOutput], bool]
    run: EnrichFn
    store: Callable[[SystemOutput], Any]
    load: Callable[[SystemOutput, Any], SystemOutput]
    estimate_usd: float = 0.02


def system_fingerprint(
    name: str, config: dict[str, Any], snapshots: dict[str, str], budget: Budget
) -> str:
    return digest(
        {
            "system": name,
            "config": config,
            "snapshot_fingerprints": dict(sorted(snapshots.items())),
            "budget": budget.model_dump(mode="json"),
        }
    )


def system_config(system: object) -> dict[str, Any]:
    config = getattr(system, "config", None)
    return dict(config()) if callable(config) else {}


@dataclass
class EvalRun:
    """Everything one invocation needs. ``snapshot_for`` maps a question to the snapshot it is
    answered on; ``index_for`` gives that snapshot's symbols (for mapping citations)."""

    system_name: str
    system: Any  # AnswerSystem or ImpactSystem
    kind: Literal["answer", "impact"]
    suite: Suite
    split: str
    items: Sequence[EvalItem]
    runs: int
    snapshot_for: Callable[[EvalItem], int | None]
    index_for: Callable[[int], SymbolIndex]
    cache: ResultCache
    fingerprint: str
    config: dict[str, Any] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)
    max_cost_usd: float | None = None
    concurrency: int = 4
    resume: bool = False
    enrichers: list[Enricher] = field(default_factory=list)
    progress: Callable[[RunRecord | Skipped], None] | None = None


async def _answer(run: EvalRun, item: EvalItem, snapshot_id: int | None) -> SystemOutput:
    started = time.perf_counter()
    try:
        if snapshot_id is None:
            raise LookupError(f"No snapshot for {item.repo_id} @ {item.commit[:12]}.")
        if run.kind == "impact":
            truth = item.truth
            if not isinstance(truth, ImpactTruth):
                raise TypeError("Impact systems answer impact questions only.")
            system: ImpactSystem = run.system
            prediction = await system.predict(
                snapshot_id, ImpactTarget(symbol_key=truth.target.symbol_key)
            )
            return SystemOutput(
                kind="impact",
                impact=prediction.ranked(),
                cost_usd=prediction.cost_usd,
                latency_s=round(time.perf_counter() - started, 3),
            )
        result = await run.system.answer(snapshot_id, item.text, run.budget)
        latency = time.perf_counter() - started
        return answer_output(result, run.index_for(snapshot_id), latency)
    except Exception as exc:  # recorded as the item's outcome, never raised
        return SystemOutput(
            kind=run.kind,
            outcome="error",
            error=f"{type(exc).__name__}: {exc}"[:500],
            latency_s=round(time.perf_counter() - started, 3),
        )


async def _enrich(
    run: EvalRun, item: EvalItem, output: SystemOutput, snapshot_id: int | None, index: int
) -> tuple[SystemOutput, bool]:
    """Apply the enrichers, from the cache where possible; returns (output, all cached)."""
    all_cached = True
    for enricher in run.enrichers:
        if output.outcome == "error" or not enricher.applies(item, output):
            continue
        key = f"{enricher.fingerprint}-{run.fingerprint}"
        cached = run.cache.get(enricher.name, key, item.id, index)
        if cached is not None:
            output = enricher.load(output, cached["data"])
            output = output.model_copy(
                update={"eval_cost_usd": output.eval_cost_usd + float(cached["cost"])}
            )
            continue
        all_cached = False
        before = output.eval_cost_usd
        try:
            output = await enricher.run(item, output, snapshot_id)
        except Exception as exc:  # the answer stands; the enrichment is missing
            note = f"{enricher.name} failed: {type(exc).__name__}: {exc}"[:300]
            output = output.model_copy(update={"notes": [*output.notes, note]})
            continue
        delta = output.eval_cost_usd - before
        run.cache.put(
            enricher.name, key, item.id, index, {"data": enricher.store(output), "cost": delta}
        )
    return output, all_cached


async def run_eval(run: EvalRun) -> RunResult:
    if run.runs < 1 or run.concurrency < 1:
        raise ValueError("runs and concurrency must be at least 1.")
    started = time.perf_counter()
    meter = CostMeter(run.max_cost_usd)
    limiter = anyio.CapacityLimiter(run.concurrency)
    lock = anyio.Lock()
    records: dict[tuple[int, str], RunRecord] = {}
    skipped: list[Skipped] = []
    first_estimate = run.budget.max_cost_usd + sum(e.estimate_usd for e in run.enrichers)

    def report(entry: RunRecord | Skipped) -> None:
        if run.progress is not None:
            run.progress(entry)

    async def one(item: EvalItem, index: int) -> None:
        async with limiter:
            snapshot_id = run.snapshot_for(item)
            cached = run.cache.get("answers", run.fingerprint, item.id, index)
            output: SystemOutput | None = None
            if cached is not None:
                output = SystemOutput.model_validate(cached)
                if output.outcome == "error" and run.resume:
                    output = None
            estimate = 0.0
            fresh = output is None
            if output is None or _needs_enrichment(run, item, output, index):
                async with lock:
                    estimate = meter.largest or first_estimate
                    if not meter.reserve(estimate):
                        entry = Skipped(question_id=item.id, run=index, reason="cost cap reached")
                        skipped.append(entry)
                        report(entry)
                        return
            spent_before = 0.0
            if output is None:
                output = await _answer(run, item, snapshot_id)
                run.cache.put(
                    "answers", run.fingerprint, item.id, index, output.model_dump(mode="json")
                )
                spent_before = output.cost_usd
            eval_before = output.eval_cost_usd
            output, enrich_cached = await _enrich(run, item, output, snapshot_id, index)
            actual = spent_before + (output.eval_cost_usd - eval_before)
            if estimate:
                async with lock:
                    meter.settle(estimate, actual)
            score = run.suite.scorer_for(item.type)(item, output).model_copy(update={"run": index})
            record = RunRecord(
                question_id=item.id,
                type=item.type,
                repo_id=item.repo_id,
                run=index,
                snapshot_id=snapshot_id,
                output=output,
                score=score,
                cached=not fresh and enrich_cached,
            )
            records[(index, item.id)] = record
            report(record)

    items = [i for i in run.items if run.kind == "answer" or i.type == "impact"]
    async with anyio.create_task_group() as group:
        for index in range(run.runs):
            for item in items:
                group.start_soon(one, item, index)

    ordered = [records[key] for key in sorted(records)]
    return RunResult(
        system=run.system_name,
        kind=run.kind,
        suite=run.suite.name,
        split=run.split,
        runs=run.runs,
        fingerprint=run.fingerprint,
        config=run.config,
        max_cost_usd=run.max_cost_usd,
        spent_usd=round(meter.spent, 6),
        records=ordered,
        skipped=sorted(skipped, key=lambda s: (s.run, s.question_id)),
        duration_s=round(time.perf_counter() - started, 2),
    )


def _needs_enrichment(run: EvalRun, item: EvalItem, output: SystemOutput, index: int) -> bool:
    if output.outcome == "error":
        return False
    return any(
        enricher.applies(item, output)
        and run.cache.get(
            enricher.name, f"{enricher.fingerprint}-{run.fingerprint}", item.id, index
        )
        is None
        for enricher in run.enrichers
    )
