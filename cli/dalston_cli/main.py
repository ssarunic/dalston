"""CLI entry point for Dalston.

Provides the main `dalston` command with global options and subcommands.
"""

from __future__ import annotations

import click
from dalston_sdk import Dalston

from dalston_cli import __version__
from dalston_cli.commands import export, jobs, listen, status, transcribe
from dalston_cli.config import load_config


@click.group()
@click.option(
    "--server",
    "-s",
    envvar="DALSTON_SERVER",
    default=None,
    help="Server URL (default: http://localhost:8000).",
)
@click.option(
    "--api-key",
    "-k",
    envvar="DALSTON_API_KEY",
    default=None,
    help="API key for authentication.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Verbose output to stderr.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress non-essential output.",
)
@click.version_option(version=__version__, prog_name="dalston")
@click.pass_context
def cli(
    ctx: click.Context,
    server: str | None,
    api_key: str | None,
    verbose: bool,
    quiet: bool,
) -> None:
    """Dalston CLI - Audio transcription from the command line.

    Submit audio files for batch transcription, capture real-time speech
    from your microphone, and manage transcription jobs.

    Configuration can be provided via:

    \b
      - CLI options (--server, --api-key)
      - Environment variables (DALSTON_SERVER, DALSTON_API_KEY)
      - Config file (~/.dalston/config.yaml)

    Examples:

    \b
      # Transcribe an audio file
      dalston transcribe meeting.mp3

    \b
      # Real-time transcription from microphone
      dalston listen

    \b
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

    # Store in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["client"] = client
    ctx.obj["config"] = config
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet


# Register commands
cli.add_command(transcribe.transcribe)
cli.add_command(listen.listen)
cli.add_command(jobs.jobs)
cli.add_command(export.export)
cli.add_command(status.status)


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
