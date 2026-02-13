"""Transcribe command for batch audio transcription."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer
from dalston_sdk import SpeakerDetection, TimestampGranularity

from dalston_cli.main import state
from dalston_cli.output import (
    error_console,
    output_job_created,
    output_transcript,
    wait_with_progress,
)

FormatType = Literal["txt", "json", "srt", "vtt"]
SpeakerMode = Literal["none", "diarize", "per-channel"]
TimestampMode = Literal["none", "segment", "word"]


def transcribe(
    files: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            help="Audio files to transcribe.",
        ),
    ],
    model: Annotated[
        str,
        typer.Option(
            "--model",
            "-m",
            help="Transcription model (e.g., whisper-large-v3, whisper-base, fast, accurate).",
        ),
    ] = "whisper-large-v3",
    language: Annotated[
        str,
        typer.Option(
            "--language",
            "-l",
            help="Language code (en, es, etc.) or 'auto' for detection.",
        ),
    ] = "auto",
    initial_prompt: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            "-p",
            help="Domain vocabulary hints (e.g., technical terms, proper names).",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output file path. For multiple files, use a directory.",
        ),
    ] = None,
    fmt: Annotated[
        FormatType,
        typer.Option(
            "--format",
            "-f",
            help="Output format.",
        ),
    ] = "txt",
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            "-w",
            help="Wait for completion (default) or return immediately.",
        ),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Machine-readable JSON output.",
        ),
    ] = False,
    speakers: Annotated[
        SpeakerMode,
        typer.Option(
            help="Speaker detection mode.",
        ),
    ] = "none",
    num_speakers: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=32,
            help="Exact number of speakers (for diarization).",
        ),
    ] = None,
    min_speakers: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=32,
            help="Minimum speakers for diarization auto-detection.",
        ),
    ] = None,
    max_speakers: Annotated[
        int | None,
        typer.Option(
            min=1,
            max=32,
            help="Maximum speakers for diarization auto-detection.",
        ),
    ] = None,
    timestamps: Annotated[
        TimestampMode,
        typer.Option(
            help="Timestamp granularity.",
        ),
    ] = "word",
    no_speakers: Annotated[
        bool,
        typer.Option(
            help="Exclude speaker labels from output.",
        ),
    ] = False,
    show_words: Annotated[
        bool,
        typer.Option(
            "--show-words",
            help="Display word-level timestamps in text output.",
        ),
    ] = False,
    retention_policy: Annotated[
        str | None,
        typer.Option(
            "--retention-policy",
            help="Retention policy name (e.g., 'short', 'long'). Uses tenant default if not specified.",
        ),
    ] = None,
) -> None:
    """Transcribe audio files.

    Submit one or more audio files for batch transcription.

    Examples:

        dalston transcribe meeting.mp3

        dalston transcribe meeting.mp3 -o transcript.txt

        dalston transcribe podcast.mp3 -f srt --speakers diarize -o podcast.srt

        dalston transcribe large.mp3 --no-wait --json

        dalston transcribe *.mp3 -f json -o transcripts/

        dalston transcribe audio.mp3 --model whisper-base  # Use faster model

        dalston transcribe audio.mp3 -m fast  # Use 'fast' alias (distil-whisper)

        dalston transcribe medical.mp3 -p "cardiology, ECG, arrhythmia"  # Domain hints

        dalston transcribe call.mp3 --speakers diarize --min-speakers 2 --max-speakers 4

        dalston transcribe audio.mp3 --show-words  # Display word-level timestamps

        dalston transcribe audio.mp3 --retention-policy short  # Use short retention policy
    """
    client = state.client
    quiet = state.quiet

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
    output_path = str(output) if output else None
    output_is_dir = output and output.is_dir()
    if len(files) > 1 and output and not output_is_dir:
        # Create directory if it doesn't exist
        output.mkdir(parents=True, exist_ok=True)
        output_is_dir = True

    for file_path in files:
        if not quiet and not json_output:
            error_console.print(f"Submitting: {file_path}")

        try:
            # Submit job
            job = client.transcribe(
                file=str(file_path),
                model=model,
                language=language,
                initial_prompt=initial_prompt,
                speaker_detection=speaker_detection,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                timestamps_granularity=timestamps_granularity,
                retention_policy=retention_policy,
            )

            if not wait:
                output_job_created(job, json_output)
                continue

            # Wait for completion with progress
            result = wait_with_progress(client, job.id, quiet or json_output)

            if result.status.value == "failed":
                error_console.print(
                    f"[red]Error:[/red] Transcription failed: {result.error or 'Unknown error'}"
                )
                raise typer.Exit(code=1)

            # Determine output path for this file
            file_output = output_path
            if output_is_dir:
                stem = file_path.stem
                file_output = str(output / f"{stem}.{fmt}")

            # Output result
            if json_output:
                output_transcript(
                    result, "json", file_output, not no_speakers, show_words
                )
            else:
                output_transcript(result, fmt, file_output, not no_speakers, show_words)

        except typer.Exit:
            raise
        except Exception as e:
            error_console.print(f"[red]Error:[/red] Error processing {file_path}: {e}")
            raise typer.Exit(code=1) from e
