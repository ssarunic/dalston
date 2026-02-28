"""Engines command for listing running engines (M36).

Shows the status of running engine runtimes and their loaded models.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from dalston_cli.main import state
from dalston_cli.output import console


def engines(
    runtime: Annotated[
        str | None,
        typer.Option(
            "--runtime",
            "-r",
            help="Filter by runtime ID (e.g., 'nemo', 'faster-whisper').",
        ),
    ] = None,
    stage: Annotated[
        str | None,
        typer.Option(
            "--stage",
            "-s",
            help="Filter by pipeline stage (e.g., 'transcribe', 'diarize').",
        ),
    ] = None,
    status_filter: Annotated[
        str | None,
        typer.Option(
            "--status",
            help="Filter by status ('running', 'available', 'unhealthy').",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """List running engines and their status.

    Shows all engine runtimes with their current status, loaded model,
    and available models that can be loaded.

    Examples:

        dalston engines

        dalston engines --runtime nemo

        dalston engines --stage transcribe

        dalston engines --status running

        dalston engines --json
    """
    client = state.client

    try:
        engine_list = client.list_engines()
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    # Apply filters
    engines_to_show = engine_list.engines

    if runtime:
        engines_to_show = [e for e in engines_to_show if e.id == runtime]

    if stage:
        engines_to_show = [e for e in engines_to_show if e.stage == stage]

    if status_filter:
        engines_to_show = [e for e in engines_to_show if e.status == status_filter]

    if as_json:
        output = {
            "engines": [
                {
                    "id": e.id,
                    "name": e.name,
                    "stage": e.stage,
                    "version": e.version,
                    "status": e.status,
                    "loaded_model": e.loaded_model,
                    "available_models": e.available_models,
                    "capabilities": {
                        "languages": e.capabilities.languages,
                        "word_timestamps": e.capabilities.word_timestamps,
                        "streaming": e.capabilities.streaming,
                    }
                    if e.capabilities
                    else None,
                }
                for e in engines_to_show
            ],
            "total": len(engines_to_show),
        }
        print(json.dumps(output, indent=2))
    else:
        if not engines_to_show:
            console.print("[dim]No engines found matching filters.[/dim]")
            return

        console.print(f"[bold]Engines ({len(engines_to_show)} total)[/bold]\n")

        for engine in engines_to_show:
            # Status indicator
            if engine.status == "running":
                status_icon = "[green]●[/green]"
            elif engine.status == "available":
                status_icon = "[yellow]○[/yellow]"
            else:
                status_icon = "[red]●[/red]"

            console.print(f"  {status_icon} [bold]{engine.id}[/bold] ({engine.stage})")
            console.print(f"      Status: {engine.status}")
            console.print(f"      Version: {engine.version}")

            if engine.loaded_model:
                console.print(f"      Loaded: [cyan]{engine.loaded_model}[/cyan]")
            else:
                console.print("      Loaded: [dim]none[/dim]")

            if engine.available_models:
                models_str = ", ".join(engine.available_models[:5])
                if len(engine.available_models) > 5:
                    models_str += f" (+{len(engine.available_models) - 5} more)"
                console.print(f"      Available: {models_str}")

            if engine.capabilities:
                caps = []
                if engine.capabilities.word_timestamps:
                    caps.append("word-ts")
                if engine.capabilities.streaming:
                    caps.append("streaming")
                if engine.capabilities.languages:
                    if len(engine.capabilities.languages) == 1:
                        caps.append(engine.capabilities.languages[0])
                    else:
                        caps.append(f"{len(engine.capabilities.languages)} langs")
                else:
                    caps.append("multilingual")

                if caps:
                    console.print(f"      Capabilities: {', '.join(caps)}")

            console.print()
