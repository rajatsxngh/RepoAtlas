"""Command line interface: ``repoatlas``."""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer
from sqlalchemy.exc import OperationalError

from repoatlas import __version__
from repoatlas.config import get_settings
from repoatlas.db import migrate
from repoatlas.db.engine import redact_url
from repoatlas.logging import configure

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
