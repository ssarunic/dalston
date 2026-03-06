"""Server lifecycle commands for local ghost-server management."""

from __future__ import annotations

import typer

from dalston_cli.bootstrap import load_bootstrap_settings
from dalston_cli.bootstrap.server_manager import (
    ServerBootstrapError,
    probe_local_server,
    stop_local_server,
)
from dalston_cli.main import state
from dalston_cli.output import console, error_console

app = typer.Typer(help="Manage local ghost server.")


@app.command("status")
def server_status() -> None:
    """Show local ghost-server status for current target."""
    client = state.client
    settings = load_bootstrap_settings(server_url=client.base_url)

    if not settings.target_is_local(client.base_url):
        console.print(
            "Current target is remote. Local ghost-server status is not applicable."
        )
        return

    probe = probe_local_server(base_url=client.base_url, settings=settings)
    console.print(f"Target: {client.base_url}")
    console.print(f"State: {probe.state.value}")
    if probe.detail:
        console.print(f"Detail: {probe.detail}")


@app.command("stop")
def stop_server() -> None:
    """Stop local ghost server started by CLI bootstrap."""
    client = state.client
    settings = load_bootstrap_settings(server_url=client.base_url)

    if not settings.target_is_local(client.base_url):
        error_console.print(
            "[yellow]Warning:[/yellow] Current --server target is remote; "
            "stopping the local ghost server anyway."
        )

    try:
        stopped = stop_local_server(settings=settings)
    except ServerBootstrapError as exc:
        error_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None

    if stopped:
        console.print("[green]✓[/green] Local ghost server stopped")
    else:
        console.print("No local ghost server PID metadata found")
