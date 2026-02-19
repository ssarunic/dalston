"""Status command for checking server and system status."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from dalston_cli.main import state
from dalston_cli.output import console


def status(
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Show server and system status.

    Displays server health, batch processing queue status, and real-time
    transcription capacity.

    Examples:

        dalston status

        dalston status --json
    """
    client = state.client
    exit_code = 0
    health_error = ""

    # Check server health
    try:
        health = client.health()
        server_healthy = health.status == "healthy"
    except Exception as e:
        server_healthy = False
        health_error = str(e)

    # Get realtime status
    realtime = None
    realtime_error = None
    try:
        realtime = client.get_realtime_status()
    except Exception as e:
        realtime_error = str(e)

    if as_json:
        data = {
            "server": client.base_url,
            "healthy": server_healthy,
        }

        if not server_healthy:
            data["error"] = health_error
            exit_code = 4

        if realtime:
            data["realtime"] = {
                "status": realtime.status,
                "active_sessions": realtime.active_sessions,
                "total_capacity": realtime.total_capacity,
                "available_capacity": realtime.available_capacity,
                "workers": realtime.worker_count,
                "ready_workers": realtime.ready_workers,
            }
        elif realtime_error:
            data["realtime"] = {"error": realtime_error}

        print(json.dumps(data, indent=2))
        raise typer.Exit(code=exit_code)

    # Human-readable output
    if server_healthy:
        console.print(f"Server: {client.base_url} [green]✓[/green]\n")
    else:
        console.print(f"Server: {client.base_url} [red]✗[/red]")
        console.print(f"[red]Error: {health_error}[/red]\n")
        raise typer.Exit(code=4)

    # Real-time status
    console.print("Real-time:")
    if realtime:
        status_color = {
            "ready": "green",
            "at_capacity": "yellow",
            "unavailable": "red",
        }.get(realtime.status, "")

        console.print(f"  Status: [{status_color}]{realtime.status}[/{status_color}]")
        console.print(
            f"  Capacity: {realtime.active_sessions}/{realtime.total_capacity} sessions "
            f"({realtime.available_capacity} available)"
        )
        console.print(
            f"  Workers: {realtime.ready_workers}/{realtime.worker_count} ready"
        )
    elif realtime_error:
        console.print(f"  [dim]Status unavailable: {realtime_error}[/dim]")
