"""Models command for listing and managing model variants (M36).

Subcommands:
- list: List available models from the catalog
- pull: Pre-download a model for faster cold start
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

import httpx
import typer
from rich.progress import BarColumn, Progress, TextColumn

from dalston_cli.main import state
from dalston_cli.output import console

app = typer.Typer(help="Manage transcription models.")


def _headers(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _format_bytes(bytes_value: Any) -> str:
    if not isinstance(bytes_value, int | float) or bytes_value < 0:
        return "-"
    if bytes_value == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(bytes_value)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    precision = 0 if idx == 0 else 1
    return f"{value:.{precision}f} {units[idx]}"


def _fetch_registry_entry(
    base_url: str, api_key: str | None, model_id: str
) -> dict[str, Any]:
    response = httpx.get(
        f"{base_url.rstrip('/')}/v1/models/{model_id}",
        headers=_headers(api_key),
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to read model '{model_id}' status (HTTP {response.status_code}): {response.text}"
        )
    return response.json()


def _trigger_pull(
    base_url: str, api_key: str | None, model_id: str, force: bool
) -> dict[str, Any]:
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/models/{model_id}/pull",
        headers=_headers(api_key),
        json={"force": force},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Failed to start model pull for '{model_id}' (HTTP {response.status_code}): {response.text}"
        )
    return response.json()


@app.command("list")
def list_models(
    model_id: Annotated[
        str | None,
        typer.Argument(
            help="Model ID to get details for. If not provided, lists all models.",
        ),
    ] = None,
    engine_id: Annotated[
        str | None,
        typer.Option(
            "--engine_id",
            "-r",
            help="Filter by engine_id (e.g., 'nemo', 'faster-whisper').",
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
    parameter in transcription requests. Each model maps to a engine_id.

    Examples:

        dalston models list

        dalston models list parakeet-tdt-1.1b

        dalston models list --engine_id nemo

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
                "engine_id": model.engine_id,
                "loaded_model_id": model.loaded_model_id,
                "source": model.source,
                "size_gb": model.size_gb,
                "stage": model.stage,
                "languages": model.capabilities.languages,
                "capabilities": {
                    "word_timestamps": model.capabilities.word_timestamps,
                    "punctuation": model.capabilities.punctuation,
                    "capitalization": model.capabilities.capitalization,
                    "native_streaming": model.capabilities.native_streaming,
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
            console.print(f"  Runtime: {model.engine_id}")
            console.print(f"  Runtime Model ID: {model.loaded_model_id}")

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

        # Filter by engine_id if specified
        models_to_show = model_list.models
        if engine_id:
            models_to_show = [m for m in models_to_show if m.engine_id == engine_id]

        if as_json:
            output = {
                "models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "engine_id": m.engine_id,
                        "loaded_model_id": m.loaded_model_id,
                        "source": m.source,
                        "size_gb": m.size_gb,
                        "languages": m.capabilities.languages,
                        "capabilities": {
                            "word_timestamps": m.capabilities.word_timestamps,
                            "punctuation": m.capabilities.punctuation,
                            "capitalization": m.capabilities.capitalization,
                            "native_streaming": m.capabilities.native_streaming,
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
            if engine_id:
                console.print(f"[bold]Models for engine_id '{engine_id}'[/bold]\n")
            else:
                console.print("[bold]Available Models[/bold]\n")

            # Group by engine_id for better display
            engine_ids: dict[str, list] = {}
            for model in models_to_show:
                rt = model.engine_id or "unknown"
                if rt not in engine_ids:
                    engine_ids[rt] = []
                engine_ids[rt].append(model)

            for rt, rt_models in sorted(engine_ids.items()):
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
    watch: Annotated[
        bool,
        typer.Option(
            "--watch/--no-watch",
            help="Watch download progress until the model reaches a terminal state.",
        ),
    ] = True,
    poll_interval: Annotated[
        float,
        typer.Option(
            "--poll-interval",
            min=0.2,
            help="Polling interval in seconds while watching progress.",
        ),
    ] = 1.0,
) -> None:
    """Request model pull from the gateway and optionally watch progress."""
    base_url = state.server
    api_key = state.api_key

    try:
        model = _fetch_registry_entry(base_url, api_key, model_id)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    console.print(f"[bold]Model:[/bold] {model.get('id', model_id)}")
    console.print(f"  Runtime: {model.get('engine_id', 'unknown')}")
    console.print(f"  Source: {model.get('source') or 'N/A'}")
    if isinstance(model.get("expected_total_bytes"), int):
        console.print(
            f"  Estimated size: {_format_bytes(model['expected_total_bytes'])}"
        )
    elif isinstance(model.get("size_bytes"), int):
        console.print(f"  Size: {_format_bytes(model['size_bytes'])}")

    try:
        pull_response = _trigger_pull(base_url, api_key, model_id, force)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from None

    status = pull_response.get("status", "unknown")
    message = pull_response.get("message", "Request accepted")
    console.print(f"[cyan]{message}[/cyan] (status: {status})")

    if not watch:
        return

    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TextColumn("• {task.fields[detail]}"),
        transient=True,
    ) as progress:
        task_id = progress.add_task(
            "Downloading", total=100.0, detail="Waiting for updates"
        )
        consecutive_failures = 0
        max_poll_failures = 5
        while True:
            try:
                model = _fetch_registry_entry(base_url, api_key, model_id)
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures >= max_poll_failures:
                    console.print(
                        f"[red]Error:[/red] {consecutive_failures} consecutive poll failures: {e}"
                    )
                    raise typer.Exit(code=1) from None
                console.print(
                    f"[yellow]Warning:[/yellow] Poll failed ({consecutive_failures}/{max_poll_failures}): {e}"
                )
                time.sleep(poll_interval)
                continue

            model_status = model.get("status", "unknown")
            pct = model.get("download_progress")
            downloaded = _format_bytes(model.get("downloaded_bytes"))
            expected = _format_bytes(model.get("expected_total_bytes"))
            detail = f"{downloaded} / {expected}"

            progress_value = float(pct) if isinstance(pct, int | float) else 0.0
            progress.update(
                task_id, completed=max(0.0, min(100.0, progress_value)), detail=detail
            )

            if model_status == "ready":
                final_size = _format_bytes(model.get("size_bytes"))
                console.print(f"[green]✓[/green] Model ready ({final_size}).")
                return
            if model_status == "failed":
                error_msg = (model.get("metadata") or {}).get("error", "unknown error")
                console.print(f"[red]Download failed:[/red] {error_msg}")
                raise typer.Exit(code=1)

            time.sleep(poll_interval)


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
