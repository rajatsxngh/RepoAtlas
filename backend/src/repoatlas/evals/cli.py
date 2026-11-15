"""``repoatlas eval``: run systems on question suites, compare reports, calibrate."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

eval_app = typer.Typer(
    help="Evaluation: score systems on the benchmark and compare them (eval/reports/).",
    no_args_is_help=True,
)


def eval_dir() -> Path:
    from repoatlas.bench.repos import benchmark_dir

    return benchmark_dir().parent


def _progress(entry: Any) -> None:
    from repoatlas.evals.runner import Skipped

    if isinstance(entry, Skipped):
        typer.echo(f"[run {entry.run}] {entry.question_id}: skipped ({entry.reason})")
        return
    output = entry.output
    score = "-" if entry.score.primary is None else f"{entry.score.primary:.3f}"
    cost = output.cost_usd + output.eval_cost_usd
    tag = " (cached)" if entry.cached else ""
    detail = f" {output.error}" if output.error else ""
    typer.echo(
        f"[run {entry.run}] {entry.question_id}: {output.outcome} {score} "
        f"${cost:.4f} {output.latency_s:.1f}s{tag}{detail}"
    )


@eval_app.command("run")
def run_command(
    system: Annotated[str, typer.Option("--system", help="A registered system name.")],
    suite: Annotated[str, typer.Option("--suite", help="Question suite.")] = "benchmark",
    split: Annotated[str, typer.Option("--split", help="dev, test, or all.")] = "dev",
    runs: Annotated[int, typer.Option("--runs", min=1, help="Runs per question.")] = 1,
    max_cost: Annotated[
        float,
        typer.Option("--max-cost", min=0.0, help="Stop starting questions past this spend (USD)."),
    ] = 1.0,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=32)] = 4,
    resume: Annotated[
        bool, typer.Option("--resume", help="Retry items that errored in an earlier run.")
    ] = False,
    snapshot: Annotated[
        list[str] | None, typer.Option("--snapshot", help="repo=ID; repeatable.")
    ] = None,
    support: Annotated[
        bool, typer.Option("--support/--no-support", help="Check claim support (costs extra).")
    ] = True,
    judge: Annotated[
        bool, typer.Option("--judge/--no-judge", help="Judge flow answers (costs extra).")
    ] = True,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Only these question IDs; repeatable.")
    ] = None,
    reports_dir: Annotated[Path | None, typer.Option("--reports-dir")] = None,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    """Answer every question of a split and write a report to eval/reports/."""
    import anyio

    from repoatlas.evals.session import EvalOptions, prepare_run, report_meta

    options = EvalOptions(
        system=system,
        suite=suite,
        split=split,
        runs=runs,
        max_cost_usd=max_cost,
        concurrency=concurrency,
        resume=resume,
        snapshots=snapshot or [],
        support=support,
        judge=judge,
        only=only or [],
        cache_dir=cache_dir or eval_dir() / "cache",
    )
    try:
        prepared = prepare_run(options, progress=_progress)
    except (LookupError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if split == "test":
        typer.echo("Test split: this run's report is kept in eval/reports/ (no cherry-picking).")
    from repoatlas.evals.report import build_report, write_report
    from repoatlas.evals.runner import run_eval

    result = anyio.run(run_eval, prepared.run)
    report = build_report(result, report_meta(prepared))
    path = write_report(report, reports_dir or eval_dir() / "reports")
    overall = report.overall
    score = (
        "-" if overall is None else f"{overall.mean:.3f} [{overall.low:.3f}, {overall.high:.3f}]"
    )
    typer.echo(
        f"{system} on {suite}/{split}: {score}; ${result.spent_usd:.2f} spent this run, "
        f"${report.total_cost_usd:.2f} in all; {'complete' if report.complete else 'INCOMPLETE'}"
    )
    typer.echo(f"Report: {path}")
    if not report.complete:
        raise typer.Exit(3)


@eval_app.command("schema")
def schema_command(
    check: Annotated[bool, typer.Option("--check", help="Only check the committed file.")] = False,
) -> None:
    """Write eval/report.schema.json from the report model."""
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.evals.report import report_schema

    path = eval_dir() / "report.schema.json"
    text = pretty_json(report_schema())
    if check:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current != text:
            typer.echo(f"{path} is out of date; run `repoatlas eval schema`.", err=True)
            raise typer.Exit(1)
        typer.echo(f"{path} is current.")
        return
    write_text_lf(path, text)
    typer.echo(f"Wrote {path}")


@eval_app.command("compare")
def compare_command(
    reports: Annotated[
        list[Path],
        typer.Argument(help="Two or more reports (JSON): the baseline first, then the others."),
    ],
    split: Annotated[
        str | None, typer.Option("--split", help="Refuse reports of another split.")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Also write markdown here.")] = None,
) -> None:
    """Per-type differences (B - A) with 95% paired bootstrap intervals, and cost per question.
    With more than two reports: every system's scores and costs (and cost per stage, where a
    system has stages), then every pair's differences."""
    from repoatlas.canonical import write_text_lf
    from repoatlas.evals.compare import (
        compare_many,
        compare_reports,
        render_comparison,
        render_many,
    )
    from repoatlas.evals.report import read_report

    if len(reports) < 2:
        typer.echo("Give at least two reports to compare.", err=True)
        raise typer.Exit(2)
    loaded = [read_report(path) for path in reports]
    if split is not None and any(report.split != split for report in loaded):
        which = "Both" if len(loaded) == 2 else "All"
        typer.echo(f"{which} reports must be on the {split} split.", err=True)
        raise typer.Exit(2)
    if len(loaded) == 2:
        text = render_comparison(compare_reports(loaded[0], loaded[1]))
    else:
        text = render_many(compare_many(loaded))
    typer.echo(text, nl=False)
    if out is not None:
        write_text_lf(out, text)


@eval_app.command("cost-report")
def cost_report_command(
    before: Annotated[
        list[Path],
        typer.Option("--before", help="A report (JSON) or a folder of reports; repeatable."),
    ],
    after: Annotated[
        list[Path] | None,
        typer.Option("--after", help="The reports after the change; repeatable."),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Also write markdown here (and JSON beside it).")
    ] = None,
    note: Annotated[
        list[str] | None, typer.Option("--note", help="A line to add under the tables.")
    ] = None,
) -> None:
    """Cost and latency per question for each report; with --after, the change per system and
    whether accuracy stayed inside the tolerance (lower bound of the 95% interval above -0.02
    for every question type). Calls no model. Exits 4 if a change is rejected."""
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.evals.costreport import build_cost_report, load_reports, render_cost_report

    try:
        before_reports, skipped = load_reports(before)
        after_reports, skipped_after = load_reports(after or [])
        if not before_reports:
            raise ValueError("No evaluation report was found for --before.")
        if after and not after_reports:
            raise ValueError("No evaluation report was found for --after.")
        report = build_cost_report(
            before_reports, after_reports, notes=[*skipped, *skipped_after, *(note or [])]
        )
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    text = render_cost_report(report)
    typer.echo(text, nl=False)
    if out is not None:
        write_text_lf(out, text)
        write_text_lf(out.with_suffix(".json"), pretty_json(report))
        typer.echo(f"Written: {out}")
    if any(change.verdict == "rejected" for change in report.changes):
        raise typer.Exit(4)


@eval_app.command("cache-estimate")
def cache_estimate_command(
    step_tokens: Annotated[
        int, typer.Option("--step-tokens", min=1, help="Tokens one exploring step adds.")
    ] = 1_500,
    role: Annotated[str, typer.Option("--role", help="small or large.")] = "small",
) -> None:
    """An estimate (arithmetic, no model) of what caching the agent's growing conversation
    saves on its exploring calls, from the price table and the size of the agent's own system
    prompt and tools. The measurement is a live-pass item."""
    import json

    from repoatlas.agent.prompting import FINAL_ANSWER_TOOL, system_prompt
    from repoatlas.agent.tools import tool_definitions
    from repoatlas.config import get_settings
    from repoatlas.evals.costreport import (
        estimate_conversation_caching,
        render_caching_estimate,
    )
    from repoatlas.retrieval.chunker import estimate_tokens

    settings = get_settings()
    if role not in ("small", "large"):
        typer.echo("--role is small or large.", err=True)
        raise typer.Exit(2)
    model = settings.llm_small_model if role == "small" else settings.llm_large_model
    price = settings.llm_prices[model]
    tools = json.dumps([*tool_definitions(), FINAL_ANSWER_TOOL])
    static = estimate_tokens(system_prompt(settings.agent_max_steps)) + estimate_tokens(tools)
    estimates = [
        estimate_conversation_caching(
            price, steps=steps, static_tokens=static, step_tokens=step_tokens
        )
        for steps in (2, 4, 6, 8, settings.agent_max_steps)
    ]
    typer.echo(render_caching_estimate(model, estimates), nl=False)


labels_app = typer.Typer(help="Human labels for the checker and the judge.", no_args_is_help=True)
eval_app.add_typer(labels_app, name="labels")


def _labels_path(name: str) -> Path:
    return eval_dir() / "labels" / name


@labels_app.command("export-claims")
def export_claims_command(
    report: Annotated[Path, typer.Option("--report", help="A dev report (JSON).")],
    n: Annotated[int, typer.Option("--n", min=1)] = 30,
    seed: Annotated[int, typer.Option("--seed")] = 7,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    """Write a blind claim-support label file (and the checker's labels beside it)."""
    from repoatlas.evals.labels import export_claims, write_label_file
    from repoatlas.evals.report import read_report
    from repoatlas.evals.runner import ResultCache
    from repoatlas.evals.session import default_context
    from repoatlas.evals.support import MAX_LINES_PER_REF

    loaded = read_report(report)
    if loaded.split != "dev":
        typer.echo("Human labels come from dev runs only.", err=True)
        raise typer.Exit(2)
    store = default_context().store
    assert store is not None

    def read(snapshot_id: int, ref: Any) -> str:
        end = min(ref.end_line, ref.start_line + MAX_LINES_PER_REF - 1)
        return str(store.read_code(snapshot_id, ref.path, ref.start_line, end).text)

    labels, machine = export_claims(
        loaded, ResultCache(cache_dir or eval_dir() / "cache"), read, n=n, seed=seed
    )
    path, machine_path = write_label_file(
        out or _labels_path("claim_support.yaml"), labels, machine
    )
    typer.echo(f"{len(labels.items)} claims to label: {path} (machine labels: {machine_path})")


@labels_app.command("agreement")
def agreement_command(
    labels_file: Annotated[Path, typer.Argument(help="A filled-in label file.")],
) -> None:
    """Accuracy and Cohen's kappa between the person's labels and the machine's."""
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.evals.labels import agreement, read_label_file, render_agreement

    labels, machine = read_label_file(labels_file)
    result = agreement(labels, machine)
    text = render_agreement(result)
    typer.echo(text, nl=False)
    write_text_lf(labels_file.with_suffix(".agreement.json"), pretty_json(result))
    write_text_lf(labels_file.with_suffix(".agreement.md"), text)


@labels_app.command("export-flow")
def export_flow_command(
    reports: Annotated[list[Path], typer.Argument(help="Dev reports of the systems to label.")],
    n: Annotated[int, typer.Option("--n", min=1)] = 20,
    seed: Annotated[int, typer.Option("--seed")] = 7,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir")] = None,
) -> None:
    """Write a blind flow-score label file from several systems' dev reports."""
    from repoatlas.evals.labels import export_flow, write_label_file
    from repoatlas.evals.report import read_report
    from repoatlas.evals.runner import ResultCache
    from repoatlas.evals.suites import load_benchmark

    loaded = [read_report(path) for path in reports]
    if any(report.split != "dev" for report in loaded):
        typer.echo("Human labels come from dev runs only.", err=True)
        raise typer.Exit(2)
    questions = {item.id: item for item in load_benchmark("dev")}
    labels, machine = export_flow(
        loaded, ResultCache(cache_dir or eval_dir() / "cache"), questions, n=n, seed=seed
    )
    path, machine_path = write_label_file(out or _labels_path("flow.yaml"), labels, machine)
    typer.echo(f"{len(labels.items)} answers to score: {path} (machine scores: {machine_path})")


@eval_app.command("embeddings")
def embeddings_command(
    models: Annotated[str, typer.Option("--models", help="Comma-separated model names.")],
    split: Annotated[str, typer.Option("--split")] = "dev",
    threads: Annotated[int | None, typer.Option("--threads", min=1)] = None,
    reports_dir: Annotated[Path | None, typer.Option("--reports-dir")] = None,
) -> None:
    """Compare embedding models: recall@k on dev lookup and callers questions, speed, memory."""
    from datetime import UTC, datetime

    from repoatlas.config import get_settings
    from repoatlas.db.engine import get_engine
    from repoatlas.evals.embeddings import compare_models, render_comparison, write_comparison
    from repoatlas.evals.snapshots import resolve_snapshots
    from repoatlas.evals.suites import load_benchmark
    from repoatlas.pipeline.stages import config_fingerprint

    if split != "dev":
        typer.echo("Embedding models are compared on dev only (tuning hygiene).", err=True)
        raise typer.Exit(2)
    items = load_benchmark(split)
    used = resolve_snapshots(
        get_engine(),
        {item.repo_id: item.commit for item in items},
        fingerprint=config_fingerprint(get_settings()),
    )
    names = [m.strip() for m in models.split(",") if m.strip()]
    comparison = compare_models(
        names, split, {repo: u.snapshot_id for repo, u in used.items()}, threads=threads
    )
    typer.echo(render_comparison(comparison), nl=False)
    date = datetime.now(UTC).date().isoformat()
    path = write_comparison(comparison, reports_dir or eval_dir() / "reports", date)
    typer.echo(f"Written: {path}")


@eval_app.command("calibrate-edges")
def calibrate_edges_command(
    repos: Annotated[list[Path], typer.Option("--repos", help="Recipe files; repeatable.")],
    fit: Annotated[
        bool,
        typer.Option("--fit/--measure-only", help="Fit confidences (calibration repos only)."),
    ] = True,
    out: Annotated[Path | None, typer.Option("--out", help="Report path stem.")] = None,
) -> None:
    """Precision of RepoAtlas edges against SCIP, per tier and provenance."""
    from datetime import UTC, datetime

    from repoatlas.bench.repos import recipes as benchmark_recipes
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.evals.calibration import (
        Calibration,
        calibrate_recipe,
        fit_confidences,
        render_calibration,
        rows,
    )
    from repoatlas.sandbox.recipe import load_recipe

    loaded = [load_recipe(path) for path in repos]
    benchmark = set(benchmark_recipes())
    if fit and any(recipe.id in benchmark for recipe in loaded):
        typer.echo("Benchmark repos are never used for fitting; pass --measure-only.", err=True)
        raise typer.Exit(2)
    measured = []
    for recipe in loaded:
        typer.echo(f"{recipe.id} @ {recipe.commit[:12]} …")
        measured.append(calibrate_recipe(recipe))
    calibration = Calibration(
        repos=measured,
        rows=rows(measured),
        fitted=fit_confidences(measured) if fit else {},
        used_for_fitting=fit,
    )
    title = "Edge calibration" if fit else "Edge precision on benchmark repos (not fitted)"
    text = render_calibration(calibration, title)
    typer.echo(text, nl=False)
    date = datetime.now(UTC).date().isoformat()
    stem = out or eval_dir() / "calibration" / f"{date}-{'fit' if fit else 'benchmark'}"
    write_text_lf(stem.with_suffix(".json"), pretty_json(calibration))
    write_text_lf(stem.with_suffix(".md"), text)
    typer.echo(f"Written: {stem.with_suffix('.json')}")
