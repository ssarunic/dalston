"""Transcribe command for batch audio transcription."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer
from dalston_sdk import (
    PIIDetectionTier,
    PIIRedactionMode,
    SpeakerDetection,
    TimestampGranularity,
)

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
PIITier = Literal["fast", "standard", "thorough"]
PIIRedactMode = Literal["silence", "beep"]


def transcribe(
    files: Annotated[
        list[Path] | None,
        typer.Argument(
            exists=True,
            help="Audio files to transcribe (optional if --url is provided).",
        ),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            "-u",
            help="URL to audio file (HTTPS, Google Drive, Dropbox, S3/GCS presigned URL).",
        ),
    ] = None,
    model: Annotated[
        str,
        typer.Option(
            "--model",
            "-m",
            help="Engine ID (e.g., faster-whisper-base, parakeet-0.6b) or 'auto' for auto-selection.",
        ),
    ] = "auto",
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
    # PII Detection Options
    pii_detection: Annotated[
        bool,
        typer.Option(
            "--pii/--no-pii",
            help="Enable PII detection in transcript.",
        ),
    ] = False,
    pii_tier: Annotated[
        PIITier | None,
        typer.Option(
            "--pii-tier",
            help="PII detection tier: fast (regex), standard (regex+ML), thorough (regex+ML+LLM).",
        ),
    ] = None,
    pii_entities: Annotated[
        str | None,
        typer.Option(
            "--pii-entities",
            help="Comma-separated PII entity types to detect (e.g., 'ssn,credit_card_number,phone_number').",
        ),
    ] = None,
    redact_audio: Annotated[
        bool,
        typer.Option(
            "--redact-audio/--no-redact-audio",
            help="Generate redacted audio file with PII removed.",
        ),
    ] = False,
    redaction_mode: Annotated[
        PIIRedactMode | None,
        typer.Option(
            "--redaction-mode",
            help="Audio redaction mode: silence or beep.",
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

        dalston transcribe call.mp3 --pii --pii-tier standard  # Detect PII entities

        dalston transcribe call.mp3 --pii --redact-audio --redaction-mode beep  # Detect and redact PII

        dalston transcribe --url "https://example.com/audio.mp3"  # Transcribe from URL

        dalston transcribe --url "https://drive.google.com/file/d/.../view"  # Google Drive URL
    """
    client = state.client
    quiet = state.quiet

    # Validate: must provide either files or url, not both or neither
    if not files and not url:
        error_console.print(
            "[red]Error:[/red] Either provide audio files or --url, not neither."
        )
        raise typer.Exit(code=1)
    if files and url:
        error_console.print(
            "[red]Error:[/red] Provide either audio files or --url, not both."
        )
        raise typer.Exit(code=1)

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

    # Map PII detection tier
    pii_detection_tier = None
    if pii_tier:
        pii_tier_map = {
            "fast": PIIDetectionTier.FAST,
            "standard": PIIDetectionTier.STANDARD,
            "thorough": PIIDetectionTier.THOROUGH,
        }
        pii_detection_tier = pii_tier_map[pii_tier]

    # Parse PII entity types
    pii_entity_types = None
    if pii_entities:
        pii_entity_types = [e.strip() for e in pii_entities.split(",")]

    # Map PII redaction mode
    pii_redaction_mode = None
    if redaction_mode:
        redaction_mode_map = {
            "silence": PIIRedactionMode.SILENCE,
            "beep": PIIRedactionMode.BEEP,
        }
        pii_redaction_mode = redaction_mode_map[redaction_mode]

    # Determine output handling for multiple files
    output_path = str(output) if output else None
    output_is_dir = output and output.is_dir()
    if files and len(files) > 1 and output and not output_is_dir:
        # Create directory if it doesn't exist
        output.mkdir(parents=True, exist_ok=True)
        output_is_dir = True

    # Build list of inputs: either file paths or a single URL
    inputs: list[tuple[str | None, str | None]] = []
    if url:
        inputs.append((None, url))  # (file_path, audio_url)
    else:
        for fp in files or []:
            inputs.append((str(fp), None))  # (file_path, audio_url)

    for file_path, audio_url in inputs:
        if not quiet and not json_output:
            if file_path:
                error_console.print(f"Submitting: {file_path}")
            else:
                error_console.print(f"Submitting URL: {audio_url[:60]}...")

        try:
            # Submit job
            job = client.transcribe(
                file=file_path,
                audio_url=audio_url,
                model=model,
                language=language,
                initial_prompt=initial_prompt,
                speaker_detection=speaker_detection,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                timestamps_granularity=timestamps_granularity,
                retention_policy=retention_policy,
                pii_detection=pii_detection,
                pii_detection_tier=pii_detection_tier,
                pii_entity_types=pii_entity_types,
                redact_pii_audio=redact_audio,
                pii_redaction_mode=pii_redaction_mode,
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
            if output_is_dir and file_path:
                stem = Path(file_path).stem
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
            source = file_path or audio_url
            error_console.print(f"[red]Error:[/red] Error processing {source}: {e}")
            raise typer.Exit(code=1) from e
