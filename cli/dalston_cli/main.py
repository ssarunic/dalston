"""CLI entry point for Dalston.

Provides the main `dalston` command with global options and subcommands.
"""

from __future__ import annotations

from typing import Annotated

import typer
from dalston_sdk import Dalston

from dalston_cli import __version__
from dalston_cli.config import load_config

app = typer.Typer(
    name="dalston",
    help="Dalston CLI - Audio transcription from the command line.",
    no_args_is_help=True,
)


class State:
    """Global state container for CLI context."""

    client: Dalston
    config: dict
    verbose: bool = False
    quiet: bool = False


state = State()


def version_callback(value: bool) -> None:
    """Handle --version flag."""
    if value:
        print(f"dalston {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    server: Annotated[
        str | None,
        typer.Option(
            "--server",
            "-s",
            envvar="DALSTON_SERVER",
            help="Server URL (default: http://localhost:8000).",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            "-k",
            envvar="DALSTON_API_KEY",
            help="API key for authentication.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Verbose output to stderr.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress non-essential output.",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Dalston CLI - Audio transcription from the command line.

    Submit audio files for batch transcription, capture real-time speech
    from your microphone, and manage transcription jobs.

    Configuration can be provided via:

        - CLI options (--server, --api-key)
        - Environment variables (DALSTON_SERVER, DALSTON_API_KEY)
        - Config file (~/.dalston/config.yaml)

    Examples:

        # Transcribe an audio file
        dalston transcribe meeting.mp3

        # Real-time transcription from microphone
        dalston listen

        # Check server status
        dalston status
    """
    # Load config from file and environment
    config = load_config()

    # Apply CLI overrides
    final_server = server or config.get("server", "http://localhost:8000")
    final_api_key = api_key or config.get("api_key")

    # Create client
    client = Dalston(base_url=final_server, api_key=final_api_key)

    # Store in global state for subcommands
    state.client = client
    state.config = config
    state.verbose = verbose
    state.quiet = quiet


# Import and register commands after app is defined
from dalston_cli.commands import export, jobs, listen, status, transcribe  # noqa: E402

app.command()(transcribe.transcribe)
app.command()(listen.listen)
app.add_typer(jobs.app, name="jobs")
app.command()(export.export)
app.command()(status.status)


def cli() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    cli()
