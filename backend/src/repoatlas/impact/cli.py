"""``repoatlas impact``: analyze a symbol or a diff, and tune the parameters on dev."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from repoatlas.impact.engine import ImpactEngine

impact_app = typer.Typer(help="Impact analysis: what breaks if code changes.", no_args_is_help=True)


def _engine() -> tuple[ImpactEngine, Engine]:
    from repoatlas.config import get_settings
    from repoatlas.db.engine import get_engine
    from repoatlas.impact.engine import ImpactEngine
    from repoatlas.impact.store_data import StoreImpactData
    from repoatlas.store.graphstore import GraphStore

    settings = get_settings()
    engine = get_engine()
    return ImpactEngine(StoreImpactData(GraphStore.from_settings(settings, engine))), engine


@impact_app.command("analyze")
def analyze_command(
    snapshot: Annotated[int, typer.Option("--snapshot", help="A ready snapshot ID.")],
    symbol: Annotated[str | None, typer.Option("--symbol", help="The symbol that changes.")] = None,
    diff: Annotated[Path | None, typer.Option("--diff", help="A unified diff file.")] = None,
    side: Annotated[str, typer.Option("--side", help="old or new (for a diff).")] = "old",
) -> None:
    """Print the impact report as JSON."""
    from repoatlas.canonical import pretty_json
    from repoatlas.impact.schema import DiffTarget, SymbolTarget

    if (symbol is None) == (diff is None):
        raise typer.BadParameter("Give exactly one of --symbol and --diff.")
    target = (
        SymbolTarget(symbol_key=symbol)
        if symbol is not None
        else DiffTarget(diff=diff.read_text(encoding="utf-8"), side=side)  # type: ignore[union-attr,arg-type]
    )
    engine, _db = _engine()
    typer.echo(pretty_json(engine.analyze(snapshot, target)))


@impact_app.command("sweep")
def sweep_command(
    split: Annotated[str, typer.Option("--split", help="Only dev may be tuned on.")] = "dev",
    snapshots: Annotated[
        list[str] | None, typer.Option("--snapshot", help="repo=ID; repeatable.")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Report path stem.")] = None,
) -> None:
    """Score a grid of max_depth x decay x min_score on the dev impact questions."""
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.config import get_settings
    from repoatlas.evals.cli import eval_dir
    from repoatlas.evals.registry import get_suite
    from repoatlas.evals.snapshots import parse_overrides, resolve_snapshots
    from repoatlas.impact.tuning import sweep, sweep_markdown
    from repoatlas.pipeline.stages import config_fingerprint

    if split != "dev":
        typer.echo("Impact parameters are tuned on the dev split only.", err=True)
        raise typer.Exit(2)
    items = [item for item in get_suite("benchmark").load(split) if item.type == "impact"]
    engine, db = _engine()
    used = resolve_snapshots(
        db,
        {item.repo_id: item.commit for item in items},
        fingerprint=config_fingerprint(get_settings()),
        overrides=parse_overrides(snapshots or []),
    )
    count = 0

    def progress(point: object) -> None:
        nonlocal count
        count += 1
        if count % 20 == 0:
            typer.echo(f"{count} points scored")

    result = sweep(
        engine, items, lambda item: used[item.repo_id].snapshot_id, split=split, progress=progress
    )
    stem = (
        out or eval_dir() / "reports" / f"{dt.date.today().isoformat()}-impact_engine-sweep-{split}"
    )
    stem.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(stem.with_suffix(".json"), pretty_json(result))
    write_text_lf(stem.with_suffix(".md"), sweep_markdown(result))
    best = result.best
    typer.echo(
        f"Best: max_depth={best.max_depth} decay={best.decay} min_score={best.min_score} "
        f"objective {best.objective:.3f} (default {result.default.objective:.3f})"
    )
    typer.echo(f"Report: {stem.with_suffix('.md')}")


@impact_app.command("unknown")
def unknown_command(
    split: Annotated[str, typer.Option("--split", help="dev or test.")] = "test",
    snapshots: Annotated[
        list[str] | None, typer.Option("--snapshot", help="repo=ID; repeatable.")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Report path stem.")] = None,
) -> None:
    """How many real failures only the unknown bucket holds, and test-file-level scores."""
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.config import get_settings
    from repoatlas.evals.cli import eval_dir
    from repoatlas.evals.registry import get_suite
    from repoatlas.evals.snapshots import parse_overrides, resolve_snapshots
    from repoatlas.impact.analysis import unknown_markdown, unknown_report
    from repoatlas.pipeline.stages import config_fingerprint

    items = [item for item in get_suite("benchmark").load(split) if item.type == "impact"]
    engine, db = _engine()
    used = resolve_snapshots(
        db,
        {item.repo_id: item.commit for item in items},
        fingerprint=config_fingerprint(get_settings()),
        overrides=parse_overrides(snapshots or []),
    )
    report = unknown_report(engine, items, lambda item: used[item.repo_id].snapshot_id, split=split)
    stem = out or eval_dir() / "reports" / (
        f"{dt.date.today().isoformat()}-impact_engine-unknown-{split}"
    )
    write_text_lf(stem.with_suffix(".json"), pretty_json(report))
    write_text_lf(stem.with_suffix(".md"), unknown_markdown(report))
    typer.echo(
        f"{report.failing} failing tests: {report.ranked} ranked, {report.unknown_only} only "
        f"in the unknown bucket, {report.missed} missed. Report: {stem.with_suffix('.md')}"
    )
