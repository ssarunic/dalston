"""Models command for listing and managing model variants (M36).

Subcommands:
- list: List available models from the catalog
- pull: Pre-download a model for faster cold start
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from dalston_cli.main import state
from dalston_cli.output import console

app = typer.Typer(help="Manage transcription models.")


@app.command("list")
def list_models(
    model_id: Annotated[
        str | None,
        typer.Argument(
            help="Model ID to get details for. If not provided, lists all models.",
        ),
    ] = None,
    runtime: Annotated[
        str | None,
        typer.Option(
            "--runtime",
            "-r",
            help="Filter by runtime (e.g., 'nemo', 'faster-whisper').",
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
    """List available transcription models from the catalog.

    Shows all available model variants that can be used with the `model`
    parameter in transcription requests. Each model maps to a runtime.

    Examples:

        dalston models list

        dalston models list parakeet-tdt-1.1b

        dalston models list --runtime nemo

        dalston models list --json
    """
    client = state.client

    if model_id:
        # Get specific model details
        try:
            model = client.get_model(model_id)
        except Exception as e:
            if as_json:
                print(json.dumps({"error": str(e)}, indent=2))
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        if as_json:
            output = {
                "id": model.id,
                "name": model.name,
                "runtime": model.runtime,
                "runtime_model_id": model.runtime_model_id,
                "source": model.source,
                "size_gb": model.size_gb,
                "stage": model.stage,
                "languages": model.capabilities.languages,
                "capabilities": {
                    "word_timestamps": model.capabilities.word_timestamps,
                    "punctuation": model.capabilities.punctuation,
                    "capitalization": model.capabilities.capitalization,
                    "streaming": model.capabilities.streaming,
                },
                "hardware": {
                    "supports_cpu": model.hardware.supports_cpu
                    if model.hardware
                    else False,
                    "min_vram_gb": model.hardware.min_vram_gb
                    if model.hardware
                    else None,
                    "min_ram_gb": model.hardware.min_ram_gb if model.hardware else None,
                }
                if model.hardware
                else None,
            }
            print(json.dumps(output, indent=2))
        else:
            console.print(f"[bold]{model.id}[/bold]")
            if model.name:
                console.print(f"  Name: {model.name}")
            console.print(f"  Runtime: {model.runtime}")
            console.print(f"  Runtime Model ID: {model.runtime_model_id}")

            if model.source:
                console.print(f"  Source: {model.source}")
            if model.size_gb:
                console.print(f"  Size: {model.size_gb} GB")

            # Languages
            if model.capabilities.languages:
                console.print(f"  Languages: {', '.join(model.capabilities.languages)}")
            else:
                console.print("  Languages: multilingual")

            # Capabilities
            console.print(f"  Word timestamps: {model.capabilities.word_timestamps}")
            if model.capabilities.punctuation:
                console.print(f"  Punctuation: {model.capabilities.punctuation}")
            if model.capabilities.capitalization:
                console.print(f"  Capitalization: {model.capabilities.capitalization}")

            # Hardware
            if model.hardware:
                console.print(f"  Supports CPU: {model.hardware.supports_cpu}")
                if model.hardware.min_vram_gb:
                    console.print(f"  Min VRAM: {model.hardware.min_vram_gb} GB")
                if model.hardware.min_ram_gb:
                    console.print(f"  Min RAM: {model.hardware.min_ram_gb} GB")
    else:
        # List all models
        try:
            model_list = client.list_models()
        except Exception as e:
            if as_json:
                print(json.dumps({"error": str(e)}, indent=2))
            else:
                console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from None

        # Filter by runtime if specified
        models_to_show = model_list.models
        if runtime:
            models_to_show = [m for m in models_to_show if m.runtime == runtime]

        if as_json:
            output = {
                "models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "runtime": m.runtime,
                        "runtime_model_id": m.runtime_model_id,
                        "source": m.source,
                        "size_gb": m.size_gb,
                        "languages": m.capabilities.languages,
                        "capabilities": {
                            "word_timestamps": m.capabilities.word_timestamps,
                            "punctuation": m.capabilities.punctuation,
                            "capitalization": m.capabilities.capitalization,
                        },
                        "hardware": {
                            "supports_cpu": m.hardware.supports_cpu,
                            "min_vram_gb": m.hardware.min_vram_gb,
                        }
                        if m.hardware
                        else None,
                    }
                    for m in models_to_show
                ],
            }
            print(json.dumps(output, indent=2))
        else:
            if runtime:
                console.print(f"[bold]Models for runtime '{runtime}'[/bold]\n")
            else:
                console.print("[bold]Available Models[/bold]\n")

            # Group by runtime for better display
            runtimes: dict[str, list] = {}
            for model in models_to_show:
                rt = model.runtime or "unknown"
                if rt not in runtimes:
                    runtimes[rt] = []
                runtimes[rt].append(model)

            for rt, rt_models in sorted(runtimes.items()):
                console.print(f"  [cyan]{rt}[/cyan]")
                for model in rt_models:
                    # Language info
                    if model.capabilities.languages:
                        lang_info = ", ".join(model.capabilities.languages)
                    else:
                        lang_info = "multilingual"

                    # Size info
                    size_info = f"{model.size_gb} GB" if model.size_gb else ""

                    # Word timestamps
                    ts_icon = (
                        "[green]T[/green]"
                        if model.capabilities.word_timestamps
                        else "[dim]-[/dim]"
                    )

                    console.print(f"    {ts_icon} [bold]{model.id}[/bold]")
                    console.print(f"      {lang_info} | {size_info}")
                console.print()


@app.command("pull")
def pull_model(
    model_id: Annotated[
        str,
        typer.Argument(
            help="Model ID to download (e.g., 'parakeet-tdt-1.1b').",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Re-download even if model already exists.",
        ),
    ] = False,
) -> None:
    """Pre-download a model for faster cold start.

    Downloads the model weights to the local cache so the engine
    doesn't need to download them on first use.

    Examples:

        dalston models pull parakeet-tdt-1.1b

        dalston models pull faster-whisper-large-v3-turbo --force
    """
    client = state.client

    # Get model details first
    try:
        model = client.get_model(model_id)
    except Exception as e:
        console.print(f"[red]Error:[/red] Model '{model_id}' not found: {e}")
        raise typer.Exit(code=1) from None

    console.print(f"[bold]Model: {model.id}[/bold]")
    console.print(f"  Runtime: {model.runtime}")
    console.print(f"  Source: {model.source or 'N/A'}")
    if model.size_gb:
        console.print(f"  Size: ~{model.size_gb} GB")
    console.print()

    # Check if model has a HuggingFace source
    if model.source and ("huggingface.co" in model.source or "/" in model.source):
        hf_model_id = model.runtime_model_id or model.source

        console.print("[yellow]Note:[/yellow] Model download requires huggingface_hub.")
        console.print()
        console.print("To download manually, run:")
        console.print()
        console.print("  [cyan]pip install huggingface_hub[/cyan]")
        console.print(f"  [cyan]huggingface-cli download {hf_model_id}[/cyan]")
        console.print()

        # Try to use huggingface_hub if available
        try:
            from huggingface_hub import snapshot_download

            console.print(f"Downloading [bold]{hf_model_id}[/bold]...")
            console.print("[dim]This may take a while for large models.[/dim]")
            console.print()

            cache_dir = snapshot_download(
                hf_model_id,
                force_download=force,
            )

            console.print(f"[green]✓[/green] Model downloaded to: {cache_dir}")

        except ImportError:
            console.print(
                "[dim]huggingface_hub not installed. "
                "Use the commands above to download manually.[/dim]"
            )
            raise typer.Exit(code=0) from None
        except Exception as e:
            console.print(f"[red]Download failed:[/red] {e}")
            raise typer.Exit(code=1) from None
    else:
        console.print(
            f"[yellow]Warning:[/yellow] No downloadable source found for '{model_id}'."
        )
        console.print("This model may be bundled with the engine container.")
        raise typer.Exit(code=0) from None


# Keep backward compatibility - allow `dalston models` without subcommand
# This shows the same output as `dalston models list`
@app.callback(invoke_without_command=True)
def models_callback(ctx: typer.Context) -> None:
    """Manage transcription models.

    Use 'dalston models list' to see available models.
    Use 'dalston models list <model_id>' to show model details.
    Use 'dalston models pull <model_id>' to pre-download a model.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand - show list
        ctx.invoke(list_models)
