"""Status command for checking server and system status."""

from __future__ import annotations

import json

import click

from dalston_cli.output import console, error_console


@click.command()
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
@click.pass_context
def status(ctx: click.Context, as_json: bool) -> None:
    """Show server and system status.

    Displays server health, batch processing queue status, and real-time
    transcription capacity.

    Examples:

        dalston status

        dalston status --json
    """
    client = ctx.obj["client"]
    exit_code = 0

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

        console.print(json.dumps(data, indent=2))
        ctx.exit(exit_code)

    # Human-readable output
    if server_healthy:
        console.print(f"Server: {client.base_url} [green]\u2713[/green]\n")
    else:
        console.print(f"Server: {client.base_url} [red]\u2717[/red]")
        console.print(f"[red]Error: {health_error}[/red]\n")
        ctx.exit(4)

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
