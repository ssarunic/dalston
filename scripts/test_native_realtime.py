#!/usr/bin/env python3
"""Test Dalston native realtime transcription endpoint.

Uses Dalston's native binary protocol (like Deepgram/AssemblyAI):
- Client sends raw binary audio frames
- Server sends JSON transcript messages

Usage:
    python scripts/test_native_realtime.py
    python scripts/test_native_realtime.py --model accurate
    python scripts/test_native_realtime.py --list-devices
    python scripts/test_native_realtime.py --store-audio --store-transcript
"""

from __future__ import annotations

import argparse
import asyncio
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
    model: str,
    language: str,
    device: int | None,
    sample_rate: int,
    store_audio: bool = False,
    store_transcript: bool = False,
) -> None:
    """Run realtime transcription session."""
    params = [
        f"api_key={api_key}",
        f"model={model}",
        f"language={language}",
        f"sample_rate={sample_rate}",
        "encoding=pcm_s16le",
        "enable_vad=true",
        "interim_results=true",
    ]

    # Add storage parameters
    if store_audio:
        params.append("store_audio=true")
    if store_transcript:
        params.append("store_transcript=true")

    ws_url = f"{url}?{'&'.join(params)}"

    print(f"Endpoint: {url}")
    print(f"Model: {model}, Language: {language}")
    if store_audio or store_transcript:
        features = []
        if store_audio:
            features.append("store_audio")
        if store_transcript:
            features.append("store_transcript")
        print(f"Features: {', '.join(features)}")
    print("-" * 50)

    mic = MicrophoneCapture(device=device, sample_rate=sample_rate)

    import time as time_module

    start_time = time_module.time()

    try:
        async with websockets.connect(ws_url, ping_interval=None) as ws:
            mic.start()
            print("[Listening... Press Ctrl+C to stop]\n")

            send_task = asyncio.create_task(send_audio(ws, mic))
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

            elapsed = time_module.time() - start_time
            print(f"[Session duration: {elapsed:.1f}s]")

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"\nConnection rejected: HTTP {e.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
    finally:
        mic.stop()
        print("\n[Session ended]")


async def send_audio(ws: websockets.ClientConnection, mic: MicrophoneCapture) -> None:
    """Send raw binary audio frames."""
    try:
        while True:
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, lambda: mic.read(timeout=0.2)
            )
            if chunk:
                await ws.send(chunk)
            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        try:
            await ws.send(json.dumps({"type": "end"}))
        except Exception:
            pass
        raise
    except websockets.exceptions.ConnectionClosed as e:
        print(
            f"\n[Send: Connection closed - code={e.code}, reason={e.reason!r}]",
            file=sys.stderr,
        )


async def receive_transcripts(ws: websockets.ClientConnection) -> None:
    """Receive and display transcripts."""
    partial_line = ""
    # Get terminal width for truncating partial results
    try:
        term_width = os.get_terminal_size().columns - 5
    except OSError:
        term_width = 75

    try:
        async for message in ws:
            if isinstance(message, bytes):
                continue

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "session.begin":
                print(f"[Session: {data.get('session_id', 'unknown')}]")

            elif msg_type == "transcript.partial":
                text = data.get("text", "")
                if text:
                    # Truncate to fit terminal (account for "... " prefix = 4 chars)
                    max_text_len = term_width - 4
                    if len(text) > max_text_len:
                        display_text = "... " + text[-max_text_len:]
                    else:
                        display_text = text
                    # Clear previous partial and print new one
                    clear = "\r" + " " * len(partial_line) + "\r"
                    partial_line = display_text
                    print(f"{clear}{partial_line}", end="", flush=True)

            elif msg_type == "transcript.final":
                text = data.get("text", "")
                if text:
                    clear = "\r" + " " * len(partial_line) + "\r"
                    partial_line = ""
                    print(f"{clear}> {text}")

            elif msg_type == "session.end":
                duration = data.get("total_duration", 0)
                print(f"\n[Session ended - duration: {duration:.1f}s]")
                break

            elif msg_type == "error":
                print(f"\n[Error: {data.get('message')}]", file=sys.stderr)

    except asyncio.CancelledError:
        raise
    except websockets.exceptions.ConnectionClosed as e:
        print(f"\n[Recv: Connection closed - code={e.code}, reason={e.reason!r}]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Dalston native realtime transcription"
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/v1/audio/transcriptions/stream",
        help="WebSocket URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DALSTON_API_KEY", "test-key"),
        help="API key",
    )
    parser.add_argument(
        "--model",
        default="auto",
        help="Model name (e.g., 'faster-whisper-large-v3', 'parakeet-rnnt-0.6b') or 'auto'",
    )
    parser.add_argument("--language", default="auto", help="Language code")
    parser.add_argument("--device", type=int, help="Audio device index")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Sample rate")
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio devices"
    )

    # Storage options
    parser.add_argument(
        "--store-audio",
        action="store_true",
        help="Record audio to S3",
    )
    parser.add_argument(
        "--store-transcript",
        action="store_true",
        help="Save transcript to S3",
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

    store_audio = args.store_audio
    store_transcript = args.store_transcript

    try:
        asyncio.run(
            transcribe(
                url=args.url,
                api_key=args.api_key,
                model=args.model,
                language=args.language,
                device=args.device,
                sample_rate=args.sample_rate,
                store_audio=store_audio,
                store_transcript=store_transcript,
            )
        )
    except KeyboardInterrupt:
        print("\n[Interrupted]")


if __name__ == "__main__":
    main()
