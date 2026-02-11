#!/usr/bin/env python3
"""Test ElevenLabs-compatible realtime transcription endpoint.

Uses ElevenLabs protocol (like OpenAI Realtime):
- Client sends JSON with base64-encoded audio
- Server sends JSON transcript messages in ElevenLabs format

Can be used against:
- Dalston's ElevenLabs-compatible endpoint
- Actual ElevenLabs API (for comparison)

Usage:
    # Test Dalston's ElevenLabs-compatible endpoint
    python scripts/test_elevenlabs_realtime.py

    # Test actual ElevenLabs API
    python scripts/test_elevenlabs_realtime.py --elevenlabs

    # List audio devices
    python scripts/test_elevenlabs_realtime.py --list-devices
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import queue
import sys
from typing import Any

import sounddevice as sd
import websockets


class MicrophoneCapture:
    """Captures audio from microphone."""

    def __init__(self, device: int | None = None, sample_rate: int = 16000):
        self.device = device
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * 0.1)  # 100ms chunks
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.chunk_size,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(
        self, indata: Any, frames: int, time: Any, status: sd.CallbackFlags
    ) -> None:
        del frames, time
        if status:
            print(f"[Audio: {status}]", file=sys.stderr)
        self._queue.put(indata.tobytes())

    def read(self, timeout: float = 0.5) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        devices = sd.query_devices()
        return [
            {
                "index": i,
                "name": d["name"],
                "channels": d["max_input_channels"],
                "sample_rate": int(d["default_samplerate"]),
            }
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]


async def transcribe(
    url: str,
    api_key: str,
    model_id: str,
    language_code: str,
    device: int | None,
    sample_rate: int,
) -> None:
    """Run realtime transcription session using ElevenLabs protocol."""
    params = "&".join(
        [
            f"api_key={api_key}",
            f"model_id={model_id}",
            f"language_code={language_code}",
            f"audio_format=pcm_{sample_rate}",
            "commit_strategy=vad",
            "include_timestamps=true",
        ]
    )
    ws_url = f"{url}?{params}"

    print(f"Endpoint: {url}")
    print(f"Model: {model_id}, Language: {language_code}")
    print("-" * 50)

    mic = MicrophoneCapture(device=device, sample_rate=sample_rate)

    try:
        async with websockets.connect(ws_url) as ws:
            mic.start()
            print("[Listening... Press Ctrl+C to stop]\n")

            send_task = asyncio.create_task(send_audio(ws, mic, sample_rate))
            recv_task = asyncio.create_task(receive_transcripts(ws))

            await asyncio.wait(
                [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
            )

            for task in [send_task, recv_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"\nConnection rejected: HTTP {e.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
    finally:
        mic.stop()
        print("\n[Session ended]")


async def send_audio(
    ws: websockets.ClientConnection, mic: MicrophoneCapture, sample_rate: int
) -> None:
    """Send base64-encoded audio in ElevenLabs format."""
    try:
        while True:
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, lambda: mic.read(timeout=0.2)
            )
            if chunk:
                message = {
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(chunk).decode("ascii"),
                    "commit": False,
                    "sample_rate": sample_rate,
                }
                await ws.send(json.dumps(message))
            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        try:
            await ws.send(json.dumps({"message_type": "close_stream"}))
        except Exception:
            pass
        raise


async def receive_transcripts(ws: websockets.ClientConnection) -> None:
    """Receive and display ElevenLabs-format transcripts."""
    partial_line = ""

    try:
        async for message in ws:
            if isinstance(message, bytes):
                continue

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("message_type")

            if msg_type == "session_started":
                print(f"[Session: {data.get('session_id', 'unknown')}]")

            elif msg_type == "partial_transcript":
                text = data.get("text", "")
                if text:
                    clear = "\r" + " " * len(partial_line) + "\r"
                    partial_line = f"... {text}"
                    print(f"{clear}{partial_line}", end="", flush=True)

            elif msg_type in (
                "committed_transcript",
                "committed_transcript_with_timestamps",
            ):
                text = data.get("text", "")
                if text:
                    clear = "\r" + " " * len(partial_line) + "\r"
                    partial_line = ""
                    print(f"{clear}> {text}")

            elif msg_type == "session_ended":
                duration = data.get("total_audio_seconds", 0)
                print(f"\n[Session ended, {duration:.1f}s audio]")
                break

            elif msg_type == "error":
                print(f"\n[Error: {data.get('error')}]", file=sys.stderr)

    except asyncio.CancelledError:
        raise
    except websockets.exceptions.ConnectionClosed:
        print("\n[Connection closed]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test ElevenLabs-compatible realtime transcription"
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/v1/speech-to-text/realtime",
        help="WebSocket URL (default: Dalston ElevenLabs-compat endpoint)",
    )
    parser.add_argument(
        "--elevenlabs",
        action="store_true",
        help="Use actual ElevenLabs API (requires ELEVENLABS_API_KEY)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DALSTON_API_KEY", "test-key"),
        help="API key",
    )
    parser.add_argument(
        "--model",
        default="scribe_v1",
        choices=["scribe_v1", "scribe_v2"],
        help="Model ID (default: scribe_v1)",
    )
    parser.add_argument("--language", default="auto", help="Language code")
    parser.add_argument("--device", type=int, help="Audio device index")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Sample rate")
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio devices"
    )

    args = parser.parse_args()

    if args.list_devices:
        devices = MicrophoneCapture.list_devices()
        if not devices:
            print("No audio input devices found.", file=sys.stderr)
            sys.exit(1)
        print("Available audio input devices:")
        for d in devices:
            print(
                f"  {d['index']}: {d['name']} ({d['channels']}ch, {d['sample_rate']}Hz)"
            )
        return

    # Determine URL and API key
    if args.elevenlabs:
        url = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            print("Error: ELEVENLABS_API_KEY required", file=sys.stderr)
            sys.exit(1)
    else:
        url = args.url
        api_key = args.api_key

    try:
        asyncio.run(
            transcribe(
                url=url,
                api_key=api_key,
                model_id=args.model,
                language_code=args.language,
                device=args.device,
                sample_rate=args.sample_rate,
            )
        )
    except KeyboardInterrupt:
        print("\n[Interrupted]")


if __name__ == "__main__":
    main()
