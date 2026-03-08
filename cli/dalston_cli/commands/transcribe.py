"""Transcribe command for batch audio transcription."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import typer
from dalston_sdk import (
    PIIRedactionMode,
    SpeakerDetection,
    TimestampGranularity,
)

from dalston_cli.bootstrap import (
    ModelBootstrapError,
    PreflightError,
    ensure_model_ready,
    load_bootstrap_settings,
    read_model_status,
    resolve_bootstrap_model,
    run_preflight,
)
from dalston_cli.bootstrap.server_manager import (
    ServerBootstrapError,
    ServerReadyResult,
    ensure_local_server_ready,
)
from dalston_cli.main import state
from dalston_cli.messages import CLIMsg
from dalston_cli.output import (
    error_console,
    output_job_created,
    output_transcript,
    wait_with_progress,
)

if TYPE_CHECKING:
    from dalston_sdk import Dalston

FormatType = Literal["txt", "json", "srt", "vtt"]
SpeakerMode = Literal["none", "diarize", "per-channel"]
TimestampMode = Literal["none", "segment", "word"]
PIIRedactMode = Literal["silence", "beep"]


def _emit_bootstrap_step(
    *,
    quiet: bool,
    json_output: bool,
    message: str,
) -> None:
    if not quiet and not json_output:
        error_console.print(f"[dim]{message}[/dim]")


def _assert_prerequisites_when_bootstrap_disabled(
    *,
    client: Dalston,
    model_id: str,
) -> None:
    try:
        health = client.health()
    except Exception as exc:
        raise ServerBootstrapError(
            CLIMsg.SERVER_NOT_REACHABLE,
            remediation=CLIMsg.SERVER_NOT_REACHABLE_REMEDIATION,
        ) from exc

    if getattr(health, "status", "") != "healthy":
        raise ServerBootstrapError(
            CLIMsg.SERVER_NOT_HEALTHY,
            remediation=CLIMsg.SERVER_NOT_HEALTHY_REMEDIATION,
        )

    if model_id.strip().lower() == "auto":
        # Server-side engine selection remains authoritative for auto model.
        return

    model_status = read_model_status(
        base_url=client.base_url,
        api_key=client.api_key,
        model_id=model_id,
    )
    if model_status.status != "ready":
        error_detail = f": {model_status.error}" if model_status.error else ""
        raise ModelBootstrapError(
            CLIMsg.MODEL_NOT_READY.format(model_id=model_id, error_detail=error_detail),
            remediation=CLIMsg.MODEL_NOT_READY_REMEDIATION.format(model_id=model_id),
        )


def _resolve_effective_model(
    *,
    requested_model: str,
    server_ready: ServerReadyResult,
    bootstrap_default_model: str,
    runtime_mode: str,
) -> str:
    """Resolve auto model for bootstrap-managed local flows.

    In distributed CLI mode, managed local bootstrap should pin the default model
    to avoid remote auto-selection variability. In lite runtime, the local server
    is authoritative for model auto-selection.
    """
    if requested_model.strip().lower() != "auto":
        return requested_model
    if not server_ready.managed:
        return requested_model
    if runtime_mode == "lite":
        return requested_model
    return resolve_bootstrap_model(requested_model, bootstrap_default_model)


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
    vocabulary: Annotated[
        list[str] | None,
        typer.Option(
            "--vocab",
            "-v",
            help="Terms to boost recognition (can be repeated: -v term1 -v term2).",
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
    # PII Detection Options
    pii_detection: Annotated[
        bool,
        typer.Option(
            "--pii/--no-pii",
            help="Enable PII detection in transcript.",
        ),
    ] = False,
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
    retention: Annotated[
        int,
        typer.Option(
            "--retention",
            "-r",
            help="Retention in days. 0=transient (no storage), -1=permanent, 1-3650=days.",
        ),
    ] = 30,
    # Lite mode profile selection (M58).  Ignored in distributed mode.
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            help=(
                "Lite mode pipeline profile: core (default), speaker, compliance. "
                "Ignored when the server is running in distributed mode."
            ),
        ),
    ] = "core",
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

        dalston transcribe medical.mp3 -v cardiology -v ECG -v arrhythmia  # Vocabulary hints

        dalston transcribe call.mp3 --speakers diarize --min-speakers 2 --max-speakers 4

        dalston transcribe audio.mp3 --show-words  # Display word-level timestamps

        dalston transcribe call.mp3 --pii  # Detect PII entities

        dalston transcribe call.mp3 --pii --redact-audio --redaction-mode beep  # Detect and redact PII

        dalston transcribe --url "https://example.com/audio.mp3"  # Transcribe from URL

        dalston transcribe --url "https://drive.google.com/file/d/.../view"  # Google Drive URL

        dalston transcribe audio.mp3 --retention 90  # Keep for 90 days

        dalston transcribe temp.mp3 --retention 0  # Transient (no storage)

        dalston transcribe important.mp3 --retention -1  # Permanent (never delete)
    """
    client = state.client
    quiet = state.quiet

    # Validate: must provide either files or url, not both or neither
    if not files and not url:
        error_console.print(CLIMsg.ERR_NO_INPUT)
        raise typer.Exit(code=1)
    if files and url:
        error_console.print(CLIMsg.ERR_FILES_AND_URL)
        raise typer.Exit(code=1)

    # Validate lite profile name client-side (fail fast before network request).
    runtime_mode = os.getenv("DALSTON_MODE", "distributed").strip().lower()
    if runtime_mode == "lite":
        try:
            from dalston.orchestrator.lite_capabilities import resolve_profile

            resolve_profile(profile)
        except Exception as exc:
            error_console.print(CLIMsg.ERR_INVALID_LITE_PROFILE.format(error=exc))
            raise typer.Exit(code=1) from None

    bootstrap_settings = load_bootstrap_settings(server_url=client.base_url)
    effective_model = model
    local_target = bootstrap_settings.target_is_local(client.base_url)

    # Pre-bootstrap for local zero-config flow
    try:
        if local_target:
            _emit_bootstrap_step(
                quiet=quiet,
                json_output=json_output,
                message=CLIMsg.BOOTSTRAP_PREFLIGHT,
            )
            run_preflight(
                files=list(files or []),
                settings=bootstrap_settings,
            )

            if not bootstrap_settings.enabled:
                _emit_bootstrap_step(
                    quiet=quiet,
                    json_output=json_output,
                    message=CLIMsg.BOOTSTRAP_DISABLED_VALIDATING,
                )
                _assert_prerequisites_when_bootstrap_disabled(
                    client=client,
                    model_id=effective_model,
                )
            else:
                _emit_bootstrap_step(
                    quiet=quiet,
                    json_output=json_output,
                    message=CLIMsg.BOOTSTRAP_ENSURING_SERVER,
                )
                server_ready = ensure_local_server_ready(
                    target_url=client.base_url,
                    settings=bootstrap_settings,
                )
                effective_model = _resolve_effective_model(
                    requested_model=model,
                    server_ready=server_ready,
                    bootstrap_default_model=bootstrap_settings.default_model,
                    runtime_mode=runtime_mode,
                )

                if effective_model.lower() == "auto":
                    _emit_bootstrap_step(
                        quiet=quiet,
                        json_output=json_output,
                        message=CLIMsg.BOOTSTRAP_SERVER_SIDE_AUTO,
                    )
                else:
                    _emit_bootstrap_step(
                        quiet=quiet,
                        json_output=json_output,
                        message=CLIMsg.BOOTSTRAP_ENSURING_MODEL.format(model=effective_model),
                    )
                    ensure_model_ready(
                        base_url=client.base_url,
                        api_key=client.api_key,
                        model_id=effective_model,
                        timeout_seconds=bootstrap_settings.model_ensure_timeout_seconds,
                    )
    except (PreflightError, ServerBootstrapError, ModelBootstrapError) as exc:
        error_console.print(CLIMsg.BOOTSTRAP_FAILED.format(error=exc))
        remediation = getattr(exc, "remediation", None)
        if remediation:
            error_console.print(CLIMsg.BOOTSTRAP_HOW_TO_FIX.format(remediation=remediation))
        raise typer.Exit(code=1) from None

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
                error_console.print(CLIMsg.SUBMITTING_FILE.format(file_path=file_path))
            else:
                error_console.print(CLIMsg.SUBMITTING_URL.format(url=audio_url[:60]))

        try:
            # Submit job
            job = client.transcribe(
                file=file_path,
                audio_url=audio_url,
                model=effective_model,
                language=language,
                vocabulary=vocabulary,
                speaker_detection=speaker_detection,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                timestamps_granularity=timestamps_granularity,
                pii_detection=pii_detection,
                pii_entity_types=pii_entity_types,
                redact_pii_audio=redact_audio,
                pii_redaction_mode=pii_redaction_mode,
                retention=retention,
                lite_profile=profile,
            )

            if not wait:
                output_job_created(job, json_output)
                continue

            # Wait for completion with progress
            result = wait_with_progress(client, job.id, quiet or json_output)

            if result.status.value == "failed":
                error_console.print(
                    CLIMsg.ERR_TRANSCRIPTION_FAILED.format(error=result.error or "Unknown error")
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
            error_console.print(CLIMsg.ERR_PROCESSING.format(source=source, error=e))
            raise typer.Exit(code=1) from e
