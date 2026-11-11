"""``repoatlas sandbox …``: build, baseline, index, and mutate the curated benchmark repos."""

from __future__ import annotations

from typing import Annotated

import typer

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
