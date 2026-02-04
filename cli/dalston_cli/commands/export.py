"""Export command for exporting transcripts in various formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer

from dalston_cli.main import state
from dalston_cli.output import console, error_console

FormatType = Literal["txt", "json", "srt", "vtt"]


def export(
    job_id: Annotated[
        str,
        typer.Argument(help="Job ID to export."),
    ],
    fmt: Annotated[
        FormatType,
        typer.Option(
            "--format",
            "-f",
            help="Export format.",
        ),
    ] = "txt",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output file path.",
        ),
    ] = None,
    no_speakers: Annotated[
        bool,
        typer.Option(
            "--no-speakers",
            help="Exclude speaker labels from output.",
        ),
    ] = False,
    max_line_length: Annotated[
        int,
        typer.Option(
            min=10,
            max=200,
            help="Maximum characters per subtitle line (for srt/vtt).",
        ),
    ] = 42,
    max_lines: Annotated[
        int,
        typer.Option(
            min=1,
            max=10,
            help="Maximum lines per subtitle block (for srt/vtt).",
        ),
    ] = 2,
) -> None:
    """Export transcript in various formats.

    Export a completed job's transcript as SRT, VTT, TXT, or JSON.

    Examples:

        dalston export abc123

        dalston export abc123 -f srt -o subtitles.srt

        dalston export abc123 -f vtt --max-line-length 60

        dalston export abc123 -f json --no-speakers
    """
    client = state.client

    try:
        content = client.export(
            job_id,
            format=fmt,
            include_speakers=not no_speakers,
            max_line_length=max_line_length,
            max_lines=max_lines,
        )

        # Handle output
        if isinstance(content, dict):
            # JSON format returns dict
            content = json.dumps(content, indent=2)

        if output:
            output.write_text(content)
            error_console.print(f"Written to {output}")
        else:
            console.print(content)

    except Exception as e:
        error_console.print(f"[red]Error:[/red] Export failed: {e}")
        raise typer.Exit(code=1) from e
