"""Listen command for real-time microphone transcription."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer
from dalston_sdk import RealtimeSession

from dalston_cli.audio import MicrophoneStream, resolve_device
from dalston_cli.main import state
from dalston_cli.output import (
    JsonlOutputHandler,
    JsonOutputHandler,
    LiveOutputHandler,
    error_console,
)

FormatType = Literal["live", "json", "jsonl"]
ModelType = Literal["fast", "accurate"]


def listen(
    language: Annotated[
        str,
        typer.Option(
            "--language",
            "-l",
            help="Language code or 'auto' for detection.",
        ),
    ] = "auto",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output file (append mode for live/jsonl, overwrite for json).",
        ),
    ] = None,
    fmt: Annotated[
        FormatType,
        typer.Option(
            "--format",
            "-f",
            help="Output format: live (human-readable), json (full session), jsonl (streaming).",
        ),
    ] = "live",
    model: Annotated[
        ModelType,
        typer.Option(
            "--model",
            "-m",
            help="Model variant: fast (lower latency) or accurate (better quality).",
        ),
    ] = "fast",
    device: Annotated[
        str | None,
        typer.Option(
            "--device",
            "-d",
            help="Audio input device (index or partial name).",
        ),
    ] = None,
    list_devices: Annotated[
        bool,
        typer.Option(
            "--list-devices",
            help="List available audio devices and exit.",
        ),
    ] = False,
    no_interim: Annotated[
        bool,
        typer.Option(
            "--no-interim",
            help="Only show final transcripts, not interim results.",
        ),
    ] = False,
    no_vad: Annotated[
        bool,
        typer.Option(
            "--no-vad",
            help="Disable voice activity detection events.",
        ),
    ] = False,
    # Storage and enhancement options
    store_audio: Annotated[
        bool,
        typer.Option(
            "--store-audio",
            help="Record audio to S3 during session.",
        ),
    ] = True,
    store_transcript: Annotated[
        bool,
        typer.Option(
            "--store-transcript",
            help="Save final transcript to S3 on session end.",
        ),
    ] = True,
    enhance: Annotated[
        bool,
        typer.Option(
            "--enhance",
            help="Run batch enhancement after session ends (requires --store-audio).",
        ),
    ] = False,
    # PII detection options
    pii: Annotated[
        bool,
        typer.Option(
            "--pii/--no-pii",
            help="Enable PII detection on enhanced transcript (requires --enhance).",
        ),
    ] = False,
    pii_tier: Annotated[
        str,
        typer.Option(
            "--pii-tier",
            help="PII detection tier: fast, standard, thorough.",
        ),
    ] = "standard",
    redact_audio: Annotated[
        bool,
        typer.Option(
            "--redact-audio",
            help="Generate redacted audio file (requires --pii).",
        ),
    ] = False,
    # File input for testing
    input_file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-i",
            help="Stream audio from file instead of microphone (for testing).",
        ),
    ] = None,
) -> None:
    """Real-time transcription from microphone.

    Captures audio from the default microphone (or specified device) and
    streams it to the Dalston server for real-time transcription.

    Press Ctrl+C to stop.

    Examples:

        dalston listen

        dalston listen -o notes.txt

        dalston listen -f jsonl | jq -r '.text'

        dalston listen --list-devices

        dalston listen -d "MacBook Pro Microphone"
    """
    # List devices and exit if requested
    if list_devices:
        devices = MicrophoneStream.list_devices()
        if not devices:
            error_console.print("No audio input devices found.")
            raise typer.Exit(code=1)

        for d in devices:
            default_marker = ""
            default_dev = MicrophoneStream.get_default_device()
            if default_dev and default_dev["index"] == d["index"]:
                default_marker = " [default]"
            error_console.print(
                f"{d['index']}: {d['name']} "
                f"({d['channels']}ch, {int(d['sample_rate'])}Hz){default_marker}"
            )
        return

    client = state.client

    # Build WebSocket URL from HTTP URL
    ws_url = client.base_url.replace("http://", "ws://").replace("https://", "wss://")

    # Resolve device if specified
    device_id = None
    if device:
        try:
            device_id = resolve_device(device)
        except ValueError as e:
            error_console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(code=1) from e

    # Create output handler based on format
    output_path = str(output) if output else None
    if fmt == "live":
        handler = LiveOutputHandler(output_path, show_interim=not no_interim)
    elif fmt == "jsonl":
        handler = JsonlOutputHandler(output_path)
    else:
        handler = JsonOutputHandler(output_path)

    # Validate PII options
    if pii and not enhance:
        error_console.print(
            "[red]Error:[/red] --pii requires --enhance (batch enhancement)"
        )
        raise typer.Exit(code=1)
    if enhance and not store_audio:
        error_console.print("[red]Error:[/red] --enhance requires --store-audio")
        raise typer.Exit(code=1)
    if redact_audio and not pii:
        error_console.print("[red]Error:[/red] --redact-audio requires --pii")
        raise typer.Exit(code=1)

    # Create real-time session with storage and PII options
    session = RealtimeSession(
        base_url=ws_url,
        api_key=client.api_key,
        language=language,
        model=model,
        enable_vad=not no_vad,
        interim_results=not no_interim,
        store_audio=store_audio,
        store_transcript=store_transcript,
        enhance_on_end=enhance,
        pii_detection=pii,
        pii_detection_tier=pii_tier,
        redact_pii_audio=redact_audio,
    )

    # Track session stats
    total_duration = 0.0
    speech_duration = 0.0

    @session.on_partial
    def on_partial(data):
        handler.partial(data.text, data.start)

    @session.on_final
    def on_final(data):
        nonlocal speech_duration
        speech_duration += data.end - data.start
        handler.final(data.text, data.start, data.end, data.confidence)

    if input_file:
        error_console.print(f"[Streaming file: {input_file}...]\n")
    else:
        error_console.print("[Listening... Press Ctrl+C to stop]\n")

    try:
        # Connect to server
        session.connect()

        if input_file:
            # Stream from file
            import time
            import wave

            try:
                with wave.open(str(input_file), "rb") as wf:
                    # Verify format
                    if wf.getsampwidth() != 2:  # 16-bit
                        error_console.print(
                            "[red]Error:[/red] Audio file must be 16-bit PCM"
                        )
                        raise typer.Exit(code=1)

                    file_sample_rate = wf.getframerate()
                    chunk_size = int(file_sample_rate * 0.1)  # 100ms chunks

                    while True:
                        frames = wf.readframes(chunk_size)
                        if not frames:
                            break
                        session.send_audio(frames)
                        # Pace the sending to match real-time
                        time.sleep(0.08)  # Slightly faster than real-time

            except wave.Error as e:
                error_console.print(f"[red]Error:[/red] Invalid audio file: {e}")
                raise typer.Exit(code=1) from e
        else:
            # Start capturing audio from microphone
            with MicrophoneStream(device=device_id) as mic:
                while True:
                    try:
                        chunk = mic.read(timeout=0.5)
                        session.send_audio(chunk)
                    except Exception:
                        # Timeout or error - continue
                        pass

    except KeyboardInterrupt:
        # Graceful shutdown
        pass
    except Exception as e:
        error_console.print(f"[red]Error:[/red] Connection error: {e}")
        raise typer.Exit(code=1) from e
    finally:
        # Close session and get stats
        try:
            end_data = session.close()
            if end_data:
                total_duration = end_data.total_audio_seconds
                if enhance:
                    error_console.print(
                        f"\n[Enhancement job created - PII detection: {pii}]"
                    )
        except Exception:
            pass

        # Output session summary
        handler.session_end(total_duration, speech_duration)
