"""Transcribe command for batch audio transcription."""

from __future__ import annotations

from pathlib import Path

import click
from dalston_sdk import SpeakerDetection, TimestampGranularity

from dalston_cli.output import (
    console,
    error_console,
    output_job_created,
    output_transcript,
    wait_with_progress,
)


@click.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--language",
    "-l",
    default="auto",
    help="Language code (en, es, etc.) or 'auto' for detection.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file path. For multiple files, use a directory.",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    default="txt",
    type=click.Choice(["txt", "json", "srt", "vtt"]),
    help="Output format.",
)
@click.option(
    "--wait/--no-wait",
    "-w",
    default=True,
    help="Wait for completion (default) or return immediately.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Machine-readable JSON output.",
)
@click.option(
    "--speakers",
    default="none",
    type=click.Choice(["none", "diarize", "per-channel"]),
    help="Speaker detection mode.",
)
@click.option(
    "--num-speakers",
    type=click.IntRange(1, 32),
    help="Expected number of speakers (for diarization).",
)
@click.option(
    "--timestamps",
    default="word",
    type=click.Choice(["none", "segment", "word"]),
    help="Timestamp granularity.",
)
@click.option(
    "--no-speakers",
    is_flag=True,
    help="Exclude speaker labels from output.",
)
@click.pass_context
def transcribe(
    ctx: click.Context,
    files: tuple[str, ...],
    language: str,
    output: str | None,
    fmt: str,
    wait: bool,
    json_output: bool,
    speakers: str,
    num_speakers: int | None,
    timestamps: str,
    no_speakers: bool,
) -> None:
    """Transcribe audio files.

    Submit one or more audio files for batch transcription.

    Examples:

        dalston transcribe meeting.mp3

        dalston transcribe meeting.mp3 -o transcript.txt

        dalston transcribe podcast.mp3 -f srt --speakers diarize -o podcast.srt

        dalston transcribe large.mp3 --no-wait --json

        dalston transcribe *.mp3 -f json -o transcripts/
    """
    client = ctx.obj["client"]
    quiet = ctx.obj["quiet"]

    # Map speaker detection string to enum
    speaker_detection_map = {
        "none": SpeakerDetection.NONE,
        "diarize": SpeakerDetection.DIARIZE,
        "per-channel": SpeakerDetection.PER_CHANNEL,
    }
    speaker_detection = speaker_detection_map[speakers]

    # Map timestamps string to enum
    timestamps_map = {
        "none": TimestampGranularity.NONE,
        "segment": TimestampGranularity.SEGMENT,
        "word": TimestampGranularity.WORD,
    }
    timestamps_granularity = timestamps_map[timestamps]

    # Determine output handling for multiple files
    output_is_dir = output and Path(output).is_dir()
    if len(files) > 1 and output and not output_is_dir:
        # Create directory if it doesn't exist
        Path(output).mkdir(parents=True, exist_ok=True)
        output_is_dir = True

    for file_path in files:
        if not quiet and not json_output:
            error_console.print(f"Submitting: {file_path}")

        try:
            # Submit job
            job = client.transcribe(
                file=file_path,
                language=language,
                speaker_detection=speaker_detection,
                num_speakers=num_speakers,
                timestamps_granularity=timestamps_granularity,
            )

            if not wait:
                output_job_created(job, json_output)
                continue

            # Wait for completion with progress
            result = wait_with_progress(client, job.id, quiet or json_output)

            if result.status.value == "failed":
                raise click.ClickException(
                    f"Transcription failed: {result.error or 'Unknown error'}"
                )

            # Determine output path for this file
            file_output = output
            if output_is_dir:
                stem = Path(file_path).stem
                file_output = str(Path(output) / f"{stem}.{fmt}")

            # Output result
            if json_output:
                output_transcript(result, "json", file_output, not no_speakers)
            else:
                output_transcript(result, fmt, file_output, not no_speakers)

        except click.ClickException:
            raise
        except Exception as e:
            raise click.ClickException(f"Error processing {file_path}: {e}") from e
