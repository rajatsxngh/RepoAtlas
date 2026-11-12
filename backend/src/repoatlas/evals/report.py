"""Evaluation reports (EVL-7): schema-validated JSON, markdown, and a chart.

``build_report(result, meta)`` turns a ``RunResult`` into a ``Report``: what was run (suite and
benchmark digests, split, system and config fingerprint, model IDs, embedding model, snapshots,
RepoAtlas commit, environment), per-type and overall metrics with 95% bootstrap intervals over
questions (each question's score averaged over runs), run-to-run means, aggregates (citation
validity, dropped claims, claim support, cost, latency), the refusal breakdown, every question's
per-run scores, and the list of incomplete or errored items.

``write_report`` saves ``eval/reports/<date>-<system>-<split>.json`` (with ``.md`` and ``.png``
next to it) and never overwrites: a second report on the same day gets ``-2``, ``-3``, …, so
every run is kept. ``eval/report.schema.json`` is generated from ``Report``
(``report_schema()``); a test keeps the committed file current, and every written report is
validated against it.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

import repoatlas
from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.evals.runner import RunRecord, RunResult, Skipped
from repoatlas.evals.scorers import Aggregates, aggregate_outputs, mean
from repoatlas.evals.stats import Interval, bootstrap_ci, question_means

SCHEMA_VERSION = "1.0"
TYPE_ORDER = ("lookup", "callers", "multihop", "impact", "flow", "unanswerable")
PRIMARY_NAMES = {
    "lookup": "correct",
    "callers": "F1",
    "multihop": "F1",
    "impact": "static F1",
    "flow": "must-cite recall",
    "unanswerable": "correct refusal",
}


class Environment(BaseModel):
    os: str
    machine: str
    cpu: str
    cpu_count: int | None
    python: str
    repoatlas_version: str
    repoatlas_commit: str | None
    repoatlas_dirty: bool | None


class SnapshotUsed(BaseModel):
    repo_id: str
    snapshot_id: int
    commit: str
    config_fingerprint: str
    embedding_model: str | None


class ReportMeta(BaseModel):
    """What the runner can't know by itself."""

    suite_info: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)
    embedding_model: str | None = None
    snapshots: list[SnapshotUsed] = Field(default_factory=list)
    evaluators: dict[str, str] = Field(default_factory=dict)  # enricher name → fingerprint
    notes: list[str] = Field(default_factory=list)


class TypeMetrics(BaseModel):
    type: str
    primary_metric: str
    questions: int  # with a score
    primary: Interval | None
    metrics: dict[str, float | None]  # means of the per-question means
    run_means: list[float | None]  # mean primary score of each run
    outcomes: dict[str, int]


class ItemRun(BaseModel):
    run: int
    outcome: str
    primary: float | None
    metrics: dict[str, float | None]
    cost_usd: float
    eval_cost_usd: float
    latency_s: float
    answer_keys: list[str]
    cited: list[str]  # compact evidence references
    support: dict[str, int]
    judge: float | None
    cached: bool
    error: str | None
    notes: list[str]


class ItemReport(BaseModel):
    question_id: str
    type: str
    repo_id: str
    snapshot_id: int | None
    mean_primary: float | None
    runs: list[ItemRun]


class ErroredItem(BaseModel):
    question_id: str
    run: int
    error: str


class Refusals(BaseModel):
    answerable: int
    answerable_refused_evidence: int
    answerable_refused_api: int
    answerable_refused_budget: int
    unanswerable: int
    unanswerable_refused_evidence: int  # correct
    unanswerable_refused_api: int
    unanswerable_answered: int


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    created_at: str
    suite: str
    suite_info: dict[str, Any]
    split: str
    system: str
    kind: Literal["answer", "impact"]
    config_fingerprint: str
    config: dict[str, Any]
    models: dict[str, str]
    embedding_model: str | None
    snapshots: list[SnapshotUsed]
    evaluators: dict[str, str]  # checker and judge fingerprints (cache keys for their results)
    environment: Environment
    runs: int
    complete: bool
    max_cost_usd: float | None
    spent_usd: float
    total_cost_usd: float
    cost_per_question_usd: float | None
    overall: Interval | None
    run_overall: list[float | None]
    by_type: list[TypeMetrics]
    aggregates: Aggregates
    refusals: Refusals
    skipped: list[Skipped]
    errored: list[ErroredItem]
    items: list[ItemReport]
    notes: list[str]


def environment() -> Environment:
    commit: str | None = None
    dirty: bool | None = None
    root = Path(__file__).resolve().parents[4]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        dirty = bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        pass
    return Environment(
        os=f"{platform.system()} {platform.release()}",
        machine=platform.machine(),
        cpu=platform.processor() or platform.machine(),
        cpu_count=os.cpu_count(),
        python=platform.python_version(),
        repoatlas_version=repoatlas.__version__,
        repoatlas_commit=commit,
        repoatlas_dirty=dirty,
    )


def _item_run(record: RunRecord) -> ItemRun:
    output = record.output
    return ItemRun(
        run=record.run,
        outcome=output.outcome,
        primary=record.score.primary,
        metrics=record.score.metrics,
        cost_usd=output.cost_usd,
        eval_cost_usd=output.eval_cost_usd,
        latency_s=output.latency_s,
        answer_keys=output.answer_keys
        if output.impact is None
        else [item.symbol_key for item in output.impact],
        cited=[ref.to_compact() for ref in output.citations],
        support=dict(sorted(Counter(label.label for label in output.support or []).items())),
        judge=output.judge.total if output.judge else None,
        cached=record.cached,
        error=output.error,
        notes=output.notes,
    )


def _refusals(records: list[RunRecord]) -> Refusals:
    answerable = [r for r in records if r.type != "unanswerable"]
    unanswerable = [r for r in records if r.type == "unanswerable"]

    def count(group: list[RunRecord], outcome: str) -> int:
        return sum(r.output.outcome == outcome for r in group)

    return Refusals(
        answerable=len(answerable),
        answerable_refused_evidence=count(answerable, "refused_evidence"),
        answerable_refused_api=count(answerable, "refused_api"),
        answerable_refused_budget=count(answerable, "refused_budget"),
        unanswerable=len(unanswerable),
        unanswerable_refused_evidence=count(unanswerable, "refused_evidence"),
        unanswerable_refused_api=count(unanswerable, "refused_api"),
        unanswerable_answered=count(unanswerable, "answered"),
    )


def _type_order(item_type: str) -> tuple[int, str]:
    return (TYPE_ORDER.index(item_type) if item_type in TYPE_ORDER else 99, item_type)


def type_metrics(records: list[RunRecord], runs: int, item_type: str) -> TypeMetrics:
    means = question_means((r.question_id, r.score.primary) for r in records)
    names = sorted({name for r in records for name in r.score.metrics})
    metrics = {
        name: mean(
            question_means((r.question_id, r.score.metrics.get(name)) for r in records).values()
        )
        for name in names
    }
    return TypeMetrics(
        type=item_type,
        primary_metric=PRIMARY_NAMES.get(item_type, "score"),
        questions=len(means),
        primary=bootstrap_ci(list(means.values())),
        metrics=metrics,
        run_means=[mean(r.score.primary for r in records if r.run == i) for i in range(runs)],
        outcomes=dict(sorted(Counter(r.output.outcome for r in records).items())),
    )


def build_report(result: RunResult, meta: ReportMeta, *, created_at: str | None = None) -> Report:
    records = result.records
    by_type: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        by_type[record.type].append(record)
    items: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        items[record.question_id].append(record)
    overall_means = question_means((r.question_id, r.score.primary) for r in records)
    aggregates = aggregate_outputs([r.output for r in records])
    question_count = len(items)
    system_cost = sum(r.output.cost_usd for r in records)
    return Report(
        created_at=created_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        suite=result.suite,
        suite_info=meta.suite_info,
        split=result.split,
        system=result.system,
        kind=result.kind,
        config_fingerprint=result.fingerprint,
        config=result.config,
        models=meta.models,
        embedding_model=meta.embedding_model,
        snapshots=meta.snapshots,
        evaluators=meta.evaluators,
        environment=environment(),
        runs=result.runs,
        complete=result.complete,
        max_cost_usd=result.max_cost_usd,
        spent_usd=result.spent_usd,
        total_cost_usd=round(result.total_cost_usd, 6),
        cost_per_question_usd=(
            round(system_cost / (question_count * result.runs), 6) if question_count else None
        ),
        overall=bootstrap_ci(list(overall_means.values())),
        run_overall=[
            mean(r.score.primary for r in records if r.run == i) for i in range(result.runs)
        ],
        by_type=[
            type_metrics(by_type[t], result.runs, t) for t in sorted(by_type, key=_type_order)
        ],
        aggregates=aggregates,
        refusals=_refusals(records),
        skipped=result.skipped,
        errored=[
            ErroredItem(question_id=r.question_id, run=r.run, error=r.output.error or "")
            for r in records
            if r.output.outcome == "error"
        ],
        items=[
            ItemReport(
                question_id=qid,
                type=group[0].type,
                repo_id=group[0].repo_id,
                snapshot_id=group[0].snapshot_id,
                mean_primary=overall_means.get(qid),
                runs=[_item_run(r) for r in sorted(group, key=lambda r: r.run)],
            )
            for qid, group in sorted(items.items())
        ],
        notes=meta.notes,
    )


# ---------------------------------------------------------------------------
# Schema and writers
# ---------------------------------------------------------------------------


def report_schema() -> dict[str, Any]:
    schema = Report.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://github.com/rajatsxngh/RepoAtlas/eval/report.schema.json"
    return schema


def validate_report(data: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    """Raises ``jsonschema.ValidationError`` when ``data`` doesn't match the report schema."""
    import jsonschema  # type: ignore[import-untyped]

    jsonschema.validate(data, schema or report_schema())


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _interval(value: Interval | None) -> str:
    if value is None:
        return "-"
    return f"{value.mean:.3f} [{value.low:.3f}, {value.high:.3f}]"


def render_markdown(report: Report) -> str:
    info = report.suite_info
    env = report.environment
    lines = [
        f"# {report.system} on {report.suite} ({report.split})",
        "",
        f"Created {report.created_at}. {'Complete.' if report.complete else '**Incomplete.**'}",
        "",
        "| | |",
        "|---|---|",
        f"| Benchmark | {info.get('benchmark_version', '-')}, questions sha256 "
        f"`{str(info.get('questions_sha256', '-'))[:16]}…`, test sha256 "
        f"`{str(info.get('test_sha256', '-'))[:16]}…` |",
        f"| System | `{report.system}` ({report.kind}), config "
        f"`{report.config_fingerprint[:16]}…` |",
        f"| Models | {', '.join(f'{k}: `{v}`' for k, v in report.models.items()) or '-'} |",
        f"| Embedding model | `{report.embedding_model or '-'}` |",
        f"| RepoAtlas | {env.repoatlas_version} @ `{(env.repoatlas_commit or '?')[:12]}`"
        f"{' (dirty)' if env.repoatlas_dirty else ''} |",
        f"| Environment | {env.os}, {env.cpu} ({env.cpu_count} CPUs), Python {env.python} |",
        f"| Runs | {report.runs} |",
        f"| Cost | ${report.total_cost_usd:.2f} in all (${report.spent_usd:.2f} new this run; "
        f"system ${_fmt(report.cost_per_question_usd, 4)} per question per run) |",
        "",
        "## Scores",
        "",
        "Means over questions (each question averaged over its runs), with 95% bootstrap "
        "intervals.",
        "",
        "| Type | Metric | Questions | Score [95% CI] | Per run |",
        "|---|---|---|---|---|",
    ]
    for t in report.by_type:
        runs = " / ".join(_fmt(v) for v in t.run_means)
        lines.append(
            f"| {t.type} | {t.primary_metric} | {t.questions} | {_interval(t.primary)} | {runs} |"
        )
    runs = " / ".join(_fmt(v) for v in report.run_overall)
    count = report.overall.n if report.overall else 0
    lines.append(f"| **all** | primary | {count} | {_interval(report.overall)} | {runs} |")
    lines += ["", "Other metrics (means):", ""]
    for t in report.by_type:
        extra = ", ".join(f"{k} {_fmt(v)}" for k, v in t.metrics.items())
        if extra:
            lines.append(f"- {t.type}: {extra}")
    agg = report.aggregates
    ref = report.refusals
    lines += [
        "",
        "## Answers",
        "",
        f"- Citation validity before filtering: mean {_fmt(agg.prefilter_citation_validity)}, "
        f"pooled {_fmt(agg.pooled_citation_validity)}; dropped-claim rate "
        f"{_fmt(agg.dropped_claim_rate)}.",
        f"- Claim support ({agg.claims_checked} claims checked): supported "
        f"{_fmt(agg.claim_supported)}, partial {_fmt(agg.claim_partial)}, unsupported "
        f"{_fmt(agg.claim_unsupported)}.",
        f"- Latency p50 {_fmt(agg.latency_p50_s, 1)} s, p95 {_fmt(agg.latency_p95_s, 1)} s.",
        f"- Outcomes: {', '.join(f'{k} {v}' for k, v in agg.outcomes.items())}.",
        f"- Refusals on answerable questions: evidence {ref.answerable_refused_evidence}, API "
        f"{ref.answerable_refused_api}, budget {ref.answerable_refused_budget} (of "
        f"{ref.answerable}). Unanswerable: refused for evidence {ref.unanswerable_refused_evidence}"
        f", API {ref.unanswerable_refused_api}, answered {ref.unanswerable_answered} (of "
        f"{ref.unanswerable}).",
    ]
    if report.skipped or report.errored:
        lines += ["", "## Incomplete", ""]
        lines += [f"- skipped {s.question_id} run {s.run}: {s.reason}" for s in report.skipped]
        lines += [f"- error {e.question_id} run {e.run}: {e.error}" for e in report.errored]
    if report.notes:
        lines += ["", "## Notes", ""]
        lines += [f"- {note}" for note in report.notes]
    return "\n".join(lines) + "\n"


def render_chart(report: Report, path: Path) -> None:
    """Horizontal bars per question type with 95% interval whiskers."""
    from PIL import Image, ImageDraw, ImageFont

    rows = [(t.type, t.primary) for t in report.by_type] + [("all", report.overall)]
    width, left, right, top, row_h = 760, 130, 40, 50, 34
    height = top + row_h * len(rows) + 40
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)
    small = ImageFont.load_default(size=12)
    title = f"{report.system} - {report.suite} {report.split} ({report.runs} runs)"
    draw.text((left, 14), title, fill="#222222", font=font)
    span = width - left - right

    def x(value: float) -> float:
        return left + span * max(0.0, min(1.0, value))

    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        draw.line([(x(tick), top - 6), (x(tick), height - 34)], fill="#e5e5e5")
        draw.text((x(tick) - 10, height - 28), f"{tick:.2f}", fill="#666666", font=small)
    for index, (label, value) in enumerate(rows):
        y = top + index * row_h
        draw.text((10, y + 8), label, fill="#222222", font=font)
        if value is None:
            continue
        color = "#3b6ea8" if label != "all" else "#1f3f66"
        draw.rectangle([(left, y + 6), (x(value.mean), y + row_h - 8)], fill=color)
        mid = y + row_h / 2 - 1
        draw.line([(x(value.low), mid), (x(value.high), mid)], fill="#111111", width=2)
        for end in (value.low, value.high):
            draw.line([(x(end), mid - 5), (x(end), mid + 5)], fill="#111111", width=2)
    image.save(path, format="PNG")


def report_stem(report: Report, directory: Path) -> Path:
    date = report.created_at[:10]
    base = f"{date}-{report.system}-{report.split}"
    candidate = directory / base
    counter = 2
    while candidate.with_suffix(".json").exists():
        candidate = directory / f"{base}-{counter}"
        counter += 1
    return candidate


def write_report(report: Report, directory: Path, *, chart: bool = True) -> Path:
    """Write ``.json``, ``.md``, and ``.png``; returns the JSON path. Never overwrites."""
    data = json.loads(report.model_dump_json())
    validate_report(data)
    stem = report_stem(report, directory)
    path = stem.with_suffix(".json")
    write_text_lf(path, pretty_json(data))
    write_text_lf(stem.with_suffix(".md"), render_markdown(report))
    if chart:
        render_chart(report, stem.with_suffix(".png"))
    return path


def read_report(path: Path) -> Report:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_report(data)
    return Report.model_validate(data)
