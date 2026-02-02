"""Export command for exporting transcripts in various formats."""

from __future__ import annotations

from pathlib import Path

import click

from dalston_cli.output import console, error_console


@click.command()
@click.argument("job_id")
@click.option(
    "--format",
    "-f",
    "fmt",
    default="txt",
    type=click.Choice(["txt", "json", "srt", "vtt"]),
    help="Export format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path.",
)
@click.option(
    "--no-speakers",
    is_flag=True,
    help="Exclude speaker labels from output.",
)
@click.option(
    "--max-line-length",
    default=42,
    type=click.IntRange(10, 200),
    help="Maximum characters per subtitle line (for srt/vtt).",
)
@click.option(
    "--max-lines",
    default=2,
    type=click.IntRange(1, 10),
    help="Maximum lines per subtitle block (for srt/vtt).",
)
@click.pass_context
def export(
    ctx: click.Context,
    job_id: str,
    fmt: str,
    output: str | None,
    no_speakers: bool,
    max_line_length: int,
    max_lines: int,
) -> None:
    """Export transcript in various formats.

    Export a completed job's transcript as SRT, VTT, TXT, or JSON.

    Examples:

        dalston export abc123

        dalston export abc123 -f srt -o subtitles.srt

        dalston export abc123 -f vtt --max-line-length 60

        dalston export abc123 -f json --no-speakers
    """
    client = ctx.obj["client"]

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
            import json

            content = json.dumps(content, indent=2)

        if output:
            Path(output).write_text(content)
            error_console.print(f"Written to {output}")
        else:
            console.print(content)

    except Exception as e:
        raise click.ClickException(f"Export failed: {e}") from e
