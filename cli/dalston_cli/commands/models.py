"""Models command for listing available transcription models."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from dalston_cli.main import state
from dalston_cli.output import console


def models(
    model_id: Annotated[
        str | None,
        typer.Argument(
            help="Model ID to get details for. If not provided, lists all models.",
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
    """List available transcription models.

    Shows all available models with their capabilities. Optionally,
    pass a model ID to get detailed information about a specific model.

    Examples:

        dalston models

        dalston models whisper-large-v3

        dalston models fast

        dalston models --json
    """
    client = state.client

    if model_id:
        # Get specific model details
        try:
            model = client.get_model(model_id)
        except Exception as e:
            if as_json:
                console.print(json.dumps({"error": str(e)}, indent=2))
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        if as_json:
            console.print(
                json.dumps(
                    {
                        "id": model.id,
                        "name": model.name,
                        "description": model.description,
                        "tier": model.tier,
                        "capabilities": {
                            "languages": model.capabilities.languages,
                            "streaming": model.capabilities.streaming,
                            "word_timestamps": model.capabilities.word_timestamps,
                        },
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[bold]{model.name}[/bold] ({model.id})")
            console.print(f"  {model.description}")
            console.print(f"  Tier: {model.tier}")
            console.print(
                f"  Languages: {model.capabilities.languages} "
                f"({'multilingual' if model.capabilities.languages > 1 else 'English only'})"
            )
            console.print(f"  Word timestamps: {model.capabilities.word_timestamps}")
            console.print(f"  Streaming: {model.capabilities.streaming}")
    else:
        # List all models
        try:
            model_list = client.list_models()
        except Exception as e:
            if as_json:
                console.print(json.dumps({"error": str(e)}, indent=2))
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        if as_json:
            console.print(
                json.dumps(
                    {
                        "models": [
                            {
                                "id": m.id,
                                "name": m.name,
                                "description": m.description,
                                "tier": m.tier,
                                "capabilities": {
                                    "languages": m.capabilities.languages,
                                    "streaming": m.capabilities.streaming,
                                    "word_timestamps": m.capabilities.word_timestamps,
                                },
                            }
                            for m in model_list.models
                        ],
                        "aliases": model_list.aliases,
                    },
                    indent=2,
                )
            )
        else:
            console.print("[bold]Available Models[/bold]\n")

            for model in model_list.models:
                tier_color = {
                    "fast": "green",
                    "balanced": "yellow",
                    "accurate": "blue",
                }.get(model.tier, "")

                lang_info = (
                    "multilingual"
                    if model.capabilities.languages > 1
                    else "English only"
                )

                console.print(f"  [bold]{model.id}[/bold]")
                console.print(f"    {model.description}")
                console.print(
                    f"    Tier: [{tier_color}]{model.tier}[/{tier_color}] | "
                    f"Languages: {lang_info}"
                )
                console.print()

            # Show aliases
            if model_list.aliases:
                console.print("[bold]Aliases[/bold]")
                for alias, target in model_list.aliases.items():
                    console.print(f"  {alias} → {target}")
