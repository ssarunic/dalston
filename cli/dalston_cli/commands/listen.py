"""Listen command for real-time microphone transcription."""

from __future__ import annotations

import click
from dalston_sdk import RealtimeSession, RealtimeMessageType

from dalston_cli.audio import MicrophoneStream, resolve_device
from dalston_cli.output import (
    JsonlOutputHandler,
    JsonOutputHandler,
    LiveOutputHandler,
    error_console,
)


@click.command()
@click.option(
    "--language",
    "-l",
    default="auto",
    help="Language code or 'auto' for detection.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Output file (append mode for live/jsonl, overwrite for json).",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    default="live",
    type=click.Choice(["live", "json", "jsonl"]),
    help="Output format: live (human-readable), json (full session), jsonl (streaming).",
)
@click.option(
    "--model",
    "-m",
    default="fast",
    type=click.Choice(["fast", "accurate"]),
    help="Model variant: fast (lower latency) or accurate (better quality).",
)
@click.option(
    "--device",
    "-d",
    help="Audio input device (index or partial name).",
)
@click.option(
    "--list-devices",
    is_flag=True,
    help="List available audio devices and exit.",
)
@click.option(
    "--no-interim",
    is_flag=True,
    help="Only show final transcripts, not interim results.",
)
@click.option(
    "--no-vad",
    is_flag=True,
    help="Disable voice activity detection events.",
)
@click.option(
    "--enhance",
    is_flag=True,
    help="Trigger batch enhancement when session ends.",
)
@click.pass_context
def listen(
    ctx: click.Context,
    language: str,
    output: str | None,
    fmt: str,
    model: str,
    device: str | None,
    list_devices: bool,
    no_interim: bool,
    no_vad: bool,
    enhance: bool,
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
            ctx.exit(1)

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

    client = ctx.obj["client"]

    # Build WebSocket URL from HTTP URL
    ws_url = client.base_url.replace("http://", "ws://").replace("https://", "wss://")

    # Resolve device if specified
    device_id = None
    if device:
        try:
            device_id = resolve_device(device)
        except ValueError as e:
            raise click.ClickException(str(e)) from e

    # Create output handler based on format
    if fmt == "live":
        handler = LiveOutputHandler(output, show_interim=not no_interim)
    elif fmt == "jsonl":
        handler = JsonlOutputHandler(output)
    else:
        handler = JsonOutputHandler(output)

    # Create real-time session
    session = RealtimeSession(
        base_url=ws_url,
        api_key=client.api_key,
        language=language,
        model=model,
        enable_vad=not no_vad,
        interim_results=not no_interim,
    )

    # Track session stats
    total_duration = 0.0
    speech_duration = 0.0
    enhancement_job_id = None

    @session.on_partial
    def on_partial(data):
        handler.partial(data.text, 0.0)

    @session.on_final
    def on_final(data):
        nonlocal speech_duration
        speech_duration += data.end - data.start
        handler.final(data.text, data.start, data.end, data.confidence)

    error_console.print("[Listening... Press Ctrl+C to stop]\n")

    try:
        # Connect to server
        session.connect()

        # Start capturing audio
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
        raise click.ClickException(f"Connection error: {e}") from e
    finally:
        # Close session and get stats
        try:
            end_data = session.close()
            if end_data:
                total_duration = end_data.total_audio_seconds
        except Exception:
            pass

        # Output session summary
        handler.session_end(total_duration, speech_duration, enhancement_job_id)
