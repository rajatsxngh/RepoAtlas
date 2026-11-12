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
    report_a: Annotated[Path, typer.Argument(help="Baseline report (JSON).")],
    report_b: Annotated[Path, typer.Argument(help="Report compared with it (JSON).")],
    split: Annotated[
        str | None, typer.Option("--split", help="Refuse reports of another split.")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Also write markdown here.")] = None,
) -> None:
    """Per-type differences (B - A) with 95% paired bootstrap intervals."""
    from repoatlas.canonical import write_text_lf
    from repoatlas.evals.compare import compare_reports, render_comparison
    from repoatlas.evals.report import read_report

    a, b = read_report(report_a), read_report(report_b)
    if split is not None and (a.split != split or b.split != split):
        typer.echo(f"Both reports must be on the {split} split.", err=True)
        raise typer.Exit(2)
    text = render_comparison(compare_reports(a, b))
    typer.echo(text, nl=False)
    if out is not None:
        write_text_lf(out, text)
