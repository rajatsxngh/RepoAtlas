"""``repoatlas sandbox …``: build, baseline, index, and mutate the curated benchmark repos."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from repoatlas.sandbox.recipe import Recipe

RecipeArg = Annotated[Path, typer.Argument(help="Recipe file, e.g. sandbox/recipes/click.yaml.")]

sandbox_app = typer.Typer(
    help="Hardened sandbox for the curated benchmark repos (decision 0012).", no_args_is_help=True
)


@sandbox_app.command("proxy")
def proxy_command(
    down: Annotated[
        bool, typer.Option("--down", help="Stop the proxy and remove the network.")
    ] = False,
) -> None:
    """Start (or stop) the internal build network and its allowlist proxy."""
    from repoatlas.sandbox.network import (
        ALLOWED_HOSTS,
        INTERNAL_NETWORK,
        PROXY_URL,
        ensure_proxy,
        proxy_down,
    )

    if down:
        proxy_down()
        typer.echo("Proxy stopped and network removed.")
        return
    image = ensure_proxy()
    typer.echo(f"Network {INTERNAL_NETWORK} (internal) with proxy {PROXY_URL} ({image[:19]}).")
    typer.echo("Allowed: " + ", ".join(ALLOWED_HOSTS))


def _load(path: Path) -> Recipe:
    from repoatlas.sandbox.recipe import RecipeError, load_recipe

    try:
        return load_recipe(path)
    except (RecipeError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@sandbox_app.command("build")
def build_command(recipe_path: RecipeArg) -> None:
    """Build the repo image: source from git objects, installs through the proxy."""
    from repoatlas.sandbox.artifacts import IMAGE_JSON, write_model
    from repoatlas.sandbox.images import build_image

    recipe = _load(recipe_path)
    info = build_image(recipe)
    path = write_model(recipe, IMAGE_JSON, info)
    typer.echo(f"{info.image.tag} {info.image.digest}")
    typer.echo(f"{info.files} files; install took {info.install_seconds} s")
    typer.echo(f"Wrote {path}")


def _image_tag(recipe: Recipe) -> str:
    from repoatlas.sandbox.artifacts import IMAGE_JSON, read_model
    from repoatlas.sandbox.docker import image_id
    from repoatlas.sandbox.images import RepoImageInfo

    info = read_model(recipe, IMAGE_JSON, RepoImageInfo)
    if info is None or image_id(info.image.tag) != info.image.digest:
        typer.echo(f"No current image for {recipe.id}; run: repoatlas sandbox build", err=True)
        raise typer.Exit(1)
    return info.image.tag


@sandbox_app.command("test")
def test_command(recipe_path: RecipeArg) -> None:
    """Run the full suite once and print a summary (a smoke check of the recipe)."""
    from collections import Counter

    from repoatlas.sandbox.runner import SandboxRunner

    recipe = _load(recipe_path)
    run = SandboxRunner(recipe, _image_tag(recipe)).run_tests()
    counts = Counter(run.outcomes().values())
    typer.echo(
        f"exit {run.exit_code} in {run.duration_s} s; "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    )
    typer.echo(f"collection errors: {len(run.collection_errors)}")
    if run.report_error:
        typer.echo(f"report: {run.report_error}")
        typer.echo(run.log_tail)
        raise typer.Exit(1)
