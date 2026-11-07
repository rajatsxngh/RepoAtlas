"""Command line interface: ``repoatlas``."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from sqlalchemy.exc import OperationalError

from repoatlas import __version__
from repoatlas.canonical import pretty_json, write_text_lf
from repoatlas.config import get_settings
from repoatlas.db import migrate
from repoatlas.db.engine import redact_url
from repoatlas.ingest.errors import IngestError
from repoatlas.logging import configure
from repoatlas.pipeline.graph import GraphOutput, run_graph
from repoatlas.pipeline.parse import ParseOutput, run_parse

START_DB_HINT = "Start it with: docker compose -f infra/docker-compose.yml up -d"

app = typer.Typer(
    name="repoatlas",
    help="RepoAtlas: code maps, cited answers, and impact analysis.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"repoatlas {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_show_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """Configure logging before any command runs."""
    configure(get_settings())


def _connection_failed(database_url: str, error: OperationalError) -> NoReturn:
    lines = str(error.orig if error.orig is not None else error).strip().splitlines()
    typer.secho(
        f"Could not connect to Postgres at {redact_url(database_url)}.",
        err=True,
        fg=typer.colors.RED,
    )
    if lines:
        typer.echo(f"Reason: {lines[0]}", err=True)
    typer.echo(f"Is it running? {START_DB_HINT}", err=True)
    raise typer.Exit(code=1)


@db_app.command("upgrade")
def db_upgrade(
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply migrations up to REVISION (default: the latest)."""
    database_url = get_settings().database_url
    try:
        migrate.upgrade(database_url, revision)
        current = migrate.current_revision(database_url)
    except OperationalError as error:
        _connection_failed(database_url, error)
    typer.echo(f"Database is at revision {current}.")


@db_app.command("current")
def db_current() -> None:
    """Show the database's current migration revision."""
    database_url = get_settings().database_url
    try:
        current = migrate.current_revision(database_url)
    except OperationalError as error:
        _connection_failed(database_url, error)
    typer.echo(current or "No migrations applied yet. Run: repoatlas db upgrade")


@db_app.command("revision")
def db_revision(
    message: Annotated[str, typer.Argument(help="Short description of the change.")],
    autogenerate: Annotated[
        bool, typer.Option(help="Compare models with the database to draft the migration.")
    ] = False,
) -> None:
    """Create a new migration file inside the package."""
    database_url = get_settings().database_url
    try:
        revision_id = migrate.create_revision(database_url, message, autogenerate=autogenerate)
    except OperationalError as error:
        _connection_failed(database_url, error)
    typer.echo(f"Created migration {revision_id}.")


@app.command("parse")
def parse_command(
    target: Annotated[str, typer.Argument(help="A public GitHub URL or a local directory.")],
    commit: Annotated[
        str | None, typer.Option(help="Full commit SHA (default: the default branch's head).")
    ] = None,
    json_out: Annotated[
        str | None,
        typer.Option("--json", help="Write the full result as JSON to this file ('-' = stdout)."),
    ] = None,
    keep_workdir: Annotated[
        bool, typer.Option(help="Keep the fetched git data for debugging.")
    ] = False,
) -> None:
    """Fetch a repository (or read a directory) and parse its source files."""
    started = time.perf_counter()
    try:
        output = run_parse(target, get_settings(), commit=commit, keep_workdir=keep_workdir)
    except IngestError as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    to_stdout = json_out == "-"
    if json_out is not None:
        text = pretty_json(output)
        if to_stdout:
            typer.echo(text, nl=False)
        else:
            write_text_lf(Path(json_out), text)
    for line in _parse_summary(output, time.perf_counter() - started):
        typer.echo(line, err=to_stdout)


def _parse_summary(output: ParseOutput, elapsed: float) -> list[str]:
    inv, stats = output.inventory.stats, output.stats
    languages = ", ".join(f"{lang} {count:,}" for lang, count in inv.indexed_by_language.items())
    skipped = ", ".join(f"{reason} {count:,}" for reason, count in inv.skipped.items()) or "none"
    issue_rate = stats.files_with_issues / stats.files_parsed if stats.files_parsed else 0.0
    commit = f" @ {output.commit}" if output.commit else ""
    return [
        f"Source:   {output.source}{commit}",
        f"Files:    {stats.files_parsed:,} parsed ({languages or 'none'}); skipped: {skipped}",
        f"Found:    {stats.symbols:,} symbols, {stats.imports:,} imports, {stats.calls:,} calls, "
        f"{stats.tests:,} tests, {stats.routes:,} routes",
        f"Issues:   {stats.files_with_issues:,} files with syntax issues ({issue_rate:.1%})",
        f"Digest:   {output.digest}",
        f"Time:     {elapsed:.1f} s",
    ]


@app.command("graph")
def graph_command(
    target: Annotated[str, typer.Argument(help="A public GitHub URL or a local directory.")],
    commit: Annotated[
        str | None, typer.Option(help="Full commit SHA (default: the default branch's head).")
    ] = None,
    json_out: Annotated[
        str | None,
        typer.Option("--json", help="Write the graph as JSON to this file ('-' = stdout)."),
    ] = None,
    stats: Annotated[
        bool, typer.Option("--stats", help="Print counts by kind, tier, and outcome.")
    ] = False,
    history: Annotated[
        bool, typer.Option(help="Fetch commit history for file owners (GitHub only).")
    ] = True,
    keep_workdir: Annotated[
        bool, typer.Option(help="Keep the fetched git data for debugging.")
    ] = False,
) -> None:
    """Build the code graph of a repository (or a directory): nodes, edges, owners."""
    started = time.perf_counter()
    try:
        output = run_graph(
            target,
            get_settings(),
            commit=commit,
            keep_workdir=keep_workdir,
            with_history=history,
        )
    except IngestError as error:
        typer.secho(f"Error: {error}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    to_stdout = json_out == "-"
    if json_out is not None:
        text = pretty_json(output)
        if to_stdout:
            typer.echo(text, nl=False)
        else:
            write_text_lf(Path(json_out), text)
    for line in _graph_summary(output, time.perf_counter() - started, detailed=stats):
        typer.echo(line, err=to_stdout)


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} {value:,}" for key, value in counts.items()) or "none"


def _graph_summary(output: GraphOutput, elapsed: float, *, detailed: bool) -> list[str]:
    stats = output.graph.stats
    commit = f" @ {output.commit}" if output.commit else ""
    nodes, edges = sum(stats.nodes_by_kind.values()), sum(stats.edges_by_kind.values())
    resolved = sum(stats.call_sites_by_tier.values())
    lines = [f"Source:   {output.source}{commit}"]
    if detailed:
        lines += [
            f"Nodes:    {nodes:,} ({_counts(stats.nodes_by_kind)})",
            f"Edges:    {edges:,} ({_counts(stats.edges_by_kind)})",
            f"Calls:    {stats.call_sites:,} sites; resolved {resolved:,} "
            f"({_counts(stats.call_sites_by_tier)})",
            f"          unresolved {stats.unresolved_calls:,} "
            f"({_counts(stats.unresolved_by_reason)}); external {stats.external_calls:,}; "
            f"builtin {stats.builtin_calls:,}",
            f"Imports:  {stats.import_statements:,} statements "
            f"({_counts(stats.imports_by_outcome)})",
            f"Owners:   {stats.files_with_owners:,} files from {stats.history_commits:,} commits",
        ]
    else:
        lines += [
            f"Graph:    {nodes:,} nodes, {edges:,} edges; {stats.unresolved_calls:,} unresolved "
            f"and {stats.external_calls:,} external calls",
        ]
    return [*lines, f"Digest:   {output.digest}", f"Time:     {elapsed:.1f} s"]
