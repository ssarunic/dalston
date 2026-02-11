"""Sessions command for managing realtime transcription sessions."""

from __future__ import annotations

from typing import Annotated, Literal

import typer
from dalston_sdk import RealtimeSessionStatus

from dalston_cli.main import state
from dalston_cli.output import console, error_console

app = typer.Typer(help="Manage realtime transcription sessions.")

StatusFilter = Literal["active", "completed", "error", "interrupted"]


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs}s"


@app.command("list")
def list_sessions(
    status: Annotated[
        StatusFilter | None,
        typer.Option(
            help="Filter by session status.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option(
            min=1,
            max=100,
            help="Maximum number of sessions to return.",
        ),
    ] = 20,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """List realtime transcription sessions.

    Shows recent sessions with their status, duration, and statistics.

    Examples:

        dalston sessions list

        dalston sessions list --status completed

        dalston sessions list --limit 50 --json
    """
    client = state.client

    # Convert status string to enum if provided
    status_filter = RealtimeSessionStatus(status) if status else None

    try:
        result = client.list_realtime_sessions(limit=limit, status=status_filter)

        if as_json:
            import json

            sessions_data = [
                {
                    "id": s.id,
                    "status": s.status.value,
                    "language": s.language,
                    "model": s.model,
                    "engine": s.engine,
                    "audio_duration_seconds": s.audio_duration_seconds,
                    "utterance_count": s.utterance_count,
                    "word_count": s.word_count,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                }
                for s in result.sessions
            ]
            print(json.dumps({"sessions": sessions_data, "total": result.total}))
        else:
            from rich.table import Table

            table = Table(title=f"Realtime Sessions ({result.total} total)")
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("Status")
            table.add_column("Model")
            table.add_column("Engine")
            table.add_column("Duration", justify="right")
            table.add_column("Utterances", justify="right")
            table.add_column("Started")

            status_colors = {
                "active": "green",
                "completed": "blue",
                "error": "red",
                "interrupted": "yellow",
            }

            for s in result.sessions:
                status_color = status_colors.get(s.status.value, "white")
                started = (
                    s.started_at.strftime("%Y-%m-%d %H:%M") if s.started_at else "-"
                )
                table.add_row(
                    s.id[:12] + "...",
                    f"[{status_color}]{s.status.value}[/{status_color}]",
                    s.model or "-",
                    s.engine or "-",
                    format_duration(s.audio_duration_seconds),
                    str(s.utterance_count),
                    started,
                )

            console.print(table)
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Failed to list sessions: {e}")
        raise typer.Exit(code=1) from e


@app.command("get")
def get_session(
    session_id: Annotated[
        str,
        typer.Argument(help="Session ID to retrieve."),
    ],
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Get session details.

    Shows detailed information about a specific session.

    Examples:

        dalston sessions get abc123

        dalston sessions get abc123 --json
    """
    client = state.client

    try:
        session = client.get_realtime_session(session_id)

        if as_json:
            import json

            data = {
                "id": session.id,
                "status": session.status.value,
                "language": session.language,
                "model": session.model,
                "engine": session.engine,
                "audio_duration_seconds": session.audio_duration_seconds,
                "utterance_count": session.utterance_count,
                "word_count": session.word_count,
                "store_audio": session.store_audio,
                "store_transcript": session.store_transcript,
                "started_at": session.started_at.isoformat()
                if session.started_at
                else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "error": session.error,
            }
            print(json.dumps(data))
        else:
            from rich.panel import Panel
            from rich.table import Table

            table = Table(show_header=False, box=None)
            table.add_column("Field", style="bold")
            table.add_column("Value")

            status_colors = {
                "active": "green",
                "completed": "blue",
                "error": "red",
                "interrupted": "yellow",
            }
            status_color = status_colors.get(session.status.value, "white")

            table.add_row("ID", session.id)
            table.add_row(
                "Status", f"[{status_color}]{session.status.value}[/{status_color}]"
            )
            table.add_row("Language", session.language or "-")
            table.add_row("Model", session.model or "-")
            table.add_row("Engine", session.engine or "-")
            table.add_row("Duration", format_duration(session.audio_duration_seconds))
            table.add_row("Utterances", str(session.utterance_count))
            table.add_row("Words", str(session.word_count))
            table.add_row("Store Audio", "Yes" if session.store_audio else "No")
            table.add_row(
                "Store Transcript", "Yes" if session.store_transcript else "No"
            )
            table.add_row(
                "Started",
                session.started_at.strftime("%Y-%m-%d %H:%M:%S")
                if session.started_at
                else "-",
            )
            table.add_row(
                "Ended",
                session.ended_at.strftime("%Y-%m-%d %H:%M:%S")
                if session.ended_at
                else "-",
            )
            if session.error:
                table.add_row("Error", f"[red]{session.error}[/red]")

            console.print(Panel(table, title="Session Details"))
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Failed to get session: {e}")
        raise typer.Exit(code=1) from e


@app.command("delete")
def delete_session(
    session_id: Annotated[
        str,
        typer.Argument(help="Session ID to delete."),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Skip confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Delete a realtime session.

    Only non-active sessions (completed, error, interrupted) can be deleted.

    Examples:

        dalston sessions delete abc123

        dalston sessions delete abc123 --force
    """
    client = state.client

    if not force:
        confirm = typer.confirm(
            f"Are you sure you want to delete session {session_id}?"
        )
        if not confirm:
            raise typer.Abort()

    try:
        client.delete_realtime_session(session_id)
        console.print(f"[green]Session {session_id} deleted[/green]")
    except Exception as e:
        error_str = str(e)
        if "active" in error_str.lower() or "409" in error_str:
            error_console.print(
                "[red]Error:[/red] Cannot delete an active session. "
                "Wait for it to complete first."
            )
        else:
            error_console.print(f"[red]Error:[/red] Failed to delete session: {e}")
        raise typer.Exit(code=1) from e
