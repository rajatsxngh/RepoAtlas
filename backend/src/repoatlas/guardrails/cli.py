"""``repoatlas guardrails``: run the injection detector on text, files, and stored snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

guardrails_app = typer.Typer(
    help="Guardrails: the injection detector (it flags text; it is not the defense).",
    no_args_is_help=True,
)


@guardrails_app.command("scan")
def scan_command(
    files: Annotated[list[Path], typer.Argument(help="Files to scan.")],
    all_flags: Annotated[
        bool, typer.Option("--all", help="Also print flags below the marking severity.")
    ] = False,
) -> None:
    """Print what the detector flags in files (and in their names)."""
    from repoatlas.guardrails.detector import MARK_SEVERITY, scan, scan_path

    found = 0
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for flag in scan_path(path.as_posix()):
            if all_flags or flag.severity >= MARK_SEVERITY:
                found += 1
                typer.echo(f"{path}: (name) {flag.kind} {flag.severity:.2f} {flag.snippet}")
        for flag in scan(text):
            if all_flags or flag.severity >= MARK_SEVERITY:
                found += 1
                line = text.count("\n", 0, flag.span[0]) + 1
                typer.echo(f"{path}:{line}: {flag.kind} {flag.severity:.2f} {flag.snippet}")
    typer.echo(f"{found} flag(s) in {len(files)} file(s).")


@guardrails_app.command("measure")
def measure_command(
    snapshot: Annotated[list[int], typer.Option("--snapshot", help="Snapshot ID; repeatable.")],
    out: Annotated[
        Path | None, typer.Option("--out", help="Report path stem (.md and .json are written).")
    ] = None,
) -> None:
    """How often the detector marks chunks of stored snapshots (its false-positive rate on
    repositories with no planted instructions)."""
    from repoatlas.canonical import pretty_json, write_text_lf
    from repoatlas.db.engine import get_engine
    from repoatlas.guardrails.measure import measure, render

    try:
        measurement = measure(get_engine(), snapshot)
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    text = render(measurement)
    typer.echo(text, nl=False)
    if out is not None:
        write_text_lf(out.with_suffix(".json"), pretty_json(measurement))
        write_text_lf(out.with_suffix(".md"), text)
        typer.echo(f"Written: {out.with_suffix('.md')}")
