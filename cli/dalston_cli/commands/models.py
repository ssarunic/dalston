"""Models command for listing available transcription engines."""

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
            help="Engine ID to get details for. If not provided, lists all engines.",
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
    """List available transcription engines.

    Shows all available engines with their capabilities. Optionally,
    pass an engine ID to get detailed information about a specific engine.

    Examples:

        dalston models

        dalston models faster-whisper-base

        dalston models parakeet-0.6b

        dalston models --json
    """
    client = state.client

    if model_id:
        # Get specific engine details
        try:
            model = client.get_model(model_id)
        except Exception as e:
            if as_json:
                console.print(json.dumps({"error": str(e)}, indent=2))
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        if as_json:
            output = {
                "id": model.id,
                "stage": model.stage,
                "capabilities": {
                    "languages": model.capabilities.languages,
                    "streaming": model.capabilities.streaming,
                    "word_timestamps": model.capabilities.word_timestamps,
                },
            }
            if model.hardware:
                output["hardware"] = {
                    "gpu_required": model.hardware.gpu_required,
                    "supports_cpu": model.hardware.supports_cpu,
                    "min_vram_gb": model.hardware.min_vram_gb,
                }
            console.print(json.dumps(output, indent=2))
        else:
            console.print(f"[bold]{model.id}[/bold]")
            console.print(f"  Stage: {model.stage}")

            # Languages
            if model.capabilities.languages:
                console.print(f"  Languages: {', '.join(model.capabilities.languages)}")
            else:
                console.print("  Languages: all (multilingual)")

            console.print(f"  Word timestamps: {model.capabilities.word_timestamps}")
            console.print(f"  Streaming: {model.capabilities.streaming}")

            if model.hardware:
                console.print(f"  GPU required: {model.hardware.gpu_required}")
                console.print(f"  Supports CPU: {model.hardware.supports_cpu}")
                if model.hardware.min_vram_gb:
                    console.print(f"  Min VRAM: {model.hardware.min_vram_gb} GB")
    else:
        # List all engines
        try:
            model_list = client.list_models()
        except Exception as e:
            if as_json:
                console.print(json.dumps({"error": str(e)}, indent=2))
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        if as_json:
            output = {
                "engines": [
                    {
                        "id": m.id,
                        "stage": m.stage,
                        "capabilities": {
                            "languages": m.capabilities.languages,
                            "streaming": m.capabilities.streaming,
                            "word_timestamps": m.capabilities.word_timestamps,
                        },
                        "hardware": {
                            "gpu_required": m.hardware.gpu_required,
                            "supports_cpu": m.hardware.supports_cpu,
                            "min_vram_gb": m.hardware.min_vram_gb,
                        }
                        if m.hardware
                        else None,
                    }
                    for m in model_list.models
                ],
            }
            console.print(json.dumps(output, indent=2))
        else:
            console.print("[bold]Available Engines[/bold]\n")

            for model in model_list.models:
                # Language info
                if model.capabilities.languages:
                    lang_info = ", ".join(model.capabilities.languages)
                else:
                    lang_info = "all languages"

                # Hardware info
                if model.hardware:
                    if model.hardware.gpu_required:
                        hw_info = "GPU required"
                    elif model.hardware.supports_cpu:
                        hw_info = "CPU supported"
                    else:
                        hw_info = ""
                else:
                    hw_info = ""

                console.print(f"  [bold]{model.id}[/bold]")
                console.print(f"    Stage: {model.stage} | Languages: {lang_info}")
                if hw_info:
                    console.print(f"    Hardware: {hw_info}")
                console.print()
