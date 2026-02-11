#!/usr/bin/env python3
"""Test Dalston's ElevenLabs-compatible API using the official ElevenLabs SDK.

This script uses the official ElevenLabs Python SDK to test compatibility.
By testing with their SDK, we can discover subtle compatibility errors that
might be missed when using raw requests.

Requirements:
    pip install elevenlabs sounddevice

Usage:
    # Test batch transcription
    python scripts/test_elevenlabs_sdk.py batch audio.mp3

    # Test realtime transcription from microphone
    python scripts/test_elevenlabs_sdk.py realtime

    # Test against actual ElevenLabs API (for comparison)
    python scripts/test_elevenlabs_sdk.py batch audio.mp3 --elevenlabs

    # List audio devices
    python scripts/test_elevenlabs_sdk.py realtime --list-devices
"""

from __future__ import annotations

import argparse
import asyncio
import os
import queue
import sys
import time
from pathlib import Path
from typing import Any

# Check for required dependencies
try:
    import sounddevice as sd
except ImportError:
    print(
        "Error: sounddevice not installed. Run: pip install sounddevice",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    from elevenlabs import ElevenLabs
except ImportError:
    print(
        "Error: elevenlabs not installed. Run: pip install elevenlabs", file=sys.stderr
    )
    sys.exit(1)


# Default Dalston endpoint (ElevenLabs-compatible)
DEFAULT_DALSTON_URL = "http://localhost:8000"


class MicrophoneCapture:
    """Captures audio from microphone for realtime transcription."""

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
        self, indata: Any, frames: int, time_info: Any, status: sd.CallbackFlags
    ) -> None:
        del frames, time_info
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


def test_batch(
    file_path: str,
    base_url: str,
    api_key: str,
    model: str = "scribe_v1",
    language: str | None = None,
    diarize: bool = False,
    num_speakers: int | None = None,
) -> None:
    """Test batch transcription using ElevenLabs SDK.

    ElevenLabs batch API: POST /v1/speech-to-text
    """
    print("Testing batch transcription with ElevenLabs SDK")
    print(f"  File: {file_path}")
    print(f"  URL: {base_url}")
    print(f"  Model: {model}")
    print(f"  Language: {language or 'auto'}")
    print(f"  Diarize: {diarize}")
    if num_speakers:
        print(f"  Num speakers: {num_speakers}")
    print("-" * 50)

    # Create ElevenLabs client pointing to Dalston
    client = ElevenLabs(
        api_key=api_key,
        base_url=base_url,
    )

    # Read audio file
    file_path = Path(file_path)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Submitting {file_path.name} for transcription...")
    start_time = time.time()

    try:
        # Use ElevenLabs SDK's speech-to-text method
        with open(file_path, "rb") as f:
            result = client.speech_to_text.convert(
                file=f,
                model_id=model,
                language_code=language,
                diarize=diarize,
                num_speakers=num_speakers,
            )

        elapsed = time.time() - start_time
        print(f"\nCompleted in {elapsed:.1f}s")
        print("=" * 50)

        # Print result
        if hasattr(result, "text"):
            print(f"\nTranscript:\n{result.text}")
        else:
            print(f"\nResult: {result}")

        # Print additional details if available
        if hasattr(result, "language_code") and result.language_code:
            print(f"\nDetected language: {result.language_code}")

        if hasattr(result, "words") and result.words:
            print(f"\nWord count: {len(result.words)}")
            # Show first few words with timestamps
            print("First 5 words:")
            for word in result.words[:5]:
                speaker = getattr(word, "speaker_id", None) or ""
                if speaker:
                    print(
                        f"  [{word.start:.2f}-{word.end:.2f}] [{speaker}] {word.text}"
                    )
                else:
                    print(f"  [{word.start:.2f}-{word.end:.2f}] {word.text}")

            # Show speaker stats if diarization was enabled
            if diarize:
                speakers = set()
                for word in result.words:
                    spk = getattr(word, "speaker_id", None)
                    if spk:
                        speakers.add(spk)
                if speakers:
                    print(f"\nSpeakers found: {sorted(speakers)}")

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        # Print more details for debugging
        if hasattr(e, "response"):
            print(f"Response: {e.response}", file=sys.stderr)
        if hasattr(e, "body"):
            print(f"Body: {e.body}", file=sys.stderr)
        sys.exit(1)


async def test_realtime(
    base_url: str,
    api_key: str,
    device: int | None = None,
    model: str = "scribe_v1_experimental",
    language: str | None = None,
    sample_rate: int = 16000,
) -> None:
    """Test realtime transcription using ElevenLabs SDK.

    ElevenLabs realtime API: WebSocket /v1/speech-to-text/realtime
    """
    print("Testing realtime transcription with ElevenLabs SDK")
    print(f"  URL: {base_url}")
    print(f"  Model: {model}")
    print(f"  Language: {language or 'auto'}")
    print(f"  Sample rate: {sample_rate}Hz")
    print("-" * 50)

    # Create ElevenLabs client pointing to Dalston
    client = ElevenLabs(
        api_key=api_key,
        base_url=base_url,
    )

    mic = MicrophoneCapture(device=device, sample_rate=sample_rate)

    try:
        # ElevenLabs SDK realtime transcription
        # The SDK uses WebSocket under the hood
        print("[Connecting to realtime endpoint...]")

        # Note: ElevenLabs SDK's realtime API might differ from their docs
        # We'll try the documented approach first
        async with client.speech_to_text.realtime.connect(
            model_id=model,
            language_code=language,
            sample_rate=sample_rate,
            encoding="pcm_s16le",
        ) as session:
            print("[Connected! Listening... Press Ctrl+C to stop]\n")
            mic.start()

            # Create tasks for sending audio and receiving transcripts
            async def send_audio():
                while True:
                    chunk = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: mic.read(timeout=0.2)
                    )
                    if chunk:
                        await session.send_audio(chunk)
                    await asyncio.sleep(0.01)

            async def receive_transcripts():
                partial_text = ""
                async for event in session:
                    if event.type == "transcript":
                        if event.is_final:
                            # Clear partial and print final
                            if partial_text:
                                print("\r" + " " * len(partial_text) + "\r", end="")
                            print(f"> {event.text}")
                            partial_text = ""
                        else:
                            # Update partial
                            if partial_text:
                                print("\r" + " " * len(partial_text) + "\r", end="")
                            partial_text = f"... {event.text}"
                            print(partial_text, end="", flush=True)
                    elif event.type == "error":
                        print(f"\n[Error: {event.message}]", file=sys.stderr)

            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(receive_transcripts())

            try:
                await asyncio.gather(send_task, recv_task)
            except asyncio.CancelledError:
                pass

    except AttributeError as e:
        # SDK might not have realtime support in the way we expect
        print(f"\nNote: ElevenLabs SDK realtime API not available: {e}")
        print("Falling back to manual WebSocket implementation...")
        await test_realtime_manual(
            base_url, api_key, device, model, language, sample_rate
        )
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        if hasattr(e, "__class__"):
            print(f"Type: {e.__class__.__name__}", file=sys.stderr)
        sys.exit(1)
    finally:
        mic.stop()
        print("\n[Session ended]")


async def test_realtime_manual(
    base_url: str,
    api_key: str,
    device: int | None = None,
    model: str = "scribe_v1_experimental",
    language: str | None = None,
    sample_rate: int = 16000,
) -> None:
    """Manual WebSocket implementation matching ElevenLabs protocol.

    Used as fallback if SDK doesn't support realtime the way we expect.
    """
    import base64
    import json

    import websockets

    # Convert HTTP URL to WebSocket URL
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_base}/v1/speech-to-text/realtime"

    # Add query parameters
    params = [f"model_id={model}", f"sample_rate={sample_rate}"]
    if language:
        params.append(f"language_code={language}")
    ws_url = f"{ws_url}?{'&'.join(params)}"

    print(f"[Connecting to {ws_url}]")

    mic = MicrophoneCapture(device=device, sample_rate=sample_rate)

    try:
        # Connect with API key in header
        headers = {"xi-api-key": api_key}
        async with websockets.connect(
            ws_url, extra_headers=headers, ping_interval=None
        ) as ws:
            print("[Connected! Listening... Press Ctrl+C to stop]\n")
            mic.start()

            async def send_audio():
                while True:
                    chunk = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: mic.read(timeout=0.2)
                    )
                    if chunk:
                        # ElevenLabs expects JSON with base64 audio
                        message = {
                            "audio": base64.b64encode(chunk).decode("utf-8"),
                        }
                        await ws.send(json.dumps(message))
                    await asyncio.sleep(0.01)

            async def receive_transcripts():
                partial_text = ""
                try:
                    async for message in ws:
                        try:
                            data = json.loads(message)
                        except json.JSONDecodeError:
                            continue

                        msg_type = data.get("type")

                        if msg_type == "transcript":
                            text = data.get("text", "")
                            is_final = data.get("is_final", False)

                            if is_final:
                                if partial_text:
                                    print("\r" + " " * len(partial_text) + "\r", end="")
                                print(f"> {text}")
                                partial_text = ""
                            else:
                                if partial_text:
                                    print("\r" + " " * len(partial_text) + "\r", end="")
                                partial_text = f"... {text}"
                                print(partial_text, end="", flush=True)

                        elif msg_type == "error":
                            print(f"\n[Error: {data.get('message')}]", file=sys.stderr)

                        elif msg_type == "session_begin":
                            print(f"[Session: {data.get('session_id', 'unknown')}]")

                except websockets.exceptions.ConnectionClosed as e:
                    print(f"\n[Connection closed: code={e.code}, reason={e.reason!r}]")

            send_task = asyncio.create_task(send_audio())
            recv_task = asyncio.create_task(receive_transcripts())

            done, pending = await asyncio.wait(
                [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test Dalston's ElevenLabs-compatible API using official SDK"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Batch command
    batch_parser = subparsers.add_parser("batch", help="Test batch transcription")
    batch_parser.add_argument("file", help="Audio file to transcribe")
    batch_parser.add_argument(
        "--url",
        default=DEFAULT_DALSTON_URL,
        help=f"Server URL (default: {DEFAULT_DALSTON_URL})",
    )
    batch_parser.add_argument(
        "--api-key",
        default=os.environ.get("DALSTON_API_KEY")
        or os.environ.get("ELEVENLABS_API_KEY", "test-key"),
        help="API key (default: from DALSTON_API_KEY or ELEVENLABS_API_KEY env)",
    )
    batch_parser.add_argument(
        "--model",
        default="scribe_v1",
        help="Model ID (default: scribe_v1)",
    )
    batch_parser.add_argument(
        "--language",
        help="Language code (default: auto-detect)",
    )
    batch_parser.add_argument(
        "--diarize",
        action="store_true",
        help="Enable speaker diarization",
    )
    batch_parser.add_argument(
        "--num-speakers",
        type=int,
        help="Expected number of speakers (1-32)",
    )
    batch_parser.add_argument(
        "--elevenlabs",
        action="store_true",
        help="Test against actual ElevenLabs API",
    )

    # Realtime command
    realtime_parser = subparsers.add_parser(
        "realtime", help="Test realtime transcription"
    )
    realtime_parser.add_argument(
        "--url",
        default=DEFAULT_DALSTON_URL,
        help=f"Server URL (default: {DEFAULT_DALSTON_URL})",
    )
    realtime_parser.add_argument(
        "--api-key",
        default=os.environ.get("DALSTON_API_KEY")
        or os.environ.get("ELEVENLABS_API_KEY", "test-key"),
        help="API key (default: from DALSTON_API_KEY or ELEVENLABS_API_KEY env)",
    )
    realtime_parser.add_argument(
        "--model",
        default="scribe_v1_experimental",
        help="Model ID (default: scribe_v1_experimental)",
    )
    realtime_parser.add_argument(
        "--language",
        help="Language code (default: auto-detect)",
    )
    realtime_parser.add_argument(
        "--device",
        type=int,
        help="Audio device index",
    )
    realtime_parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="Sample rate (default: 16000)",
    )
    realtime_parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices",
    )
    realtime_parser.add_argument(
        "--elevenlabs",
        action="store_true",
        help="Test against actual ElevenLabs API",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "batch":
        url = "https://api.elevenlabs.io" if args.elevenlabs else args.url
        test_batch(
            file_path=args.file,
            base_url=url,
            api_key=args.api_key,
            model=args.model,
            language=args.language,
            diarize=args.diarize,
            num_speakers=args.num_speakers,
        )

    elif args.command == "realtime":
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

        url = "https://api.elevenlabs.io" if args.elevenlabs else args.url
        try:
            asyncio.run(
                test_realtime(
                    base_url=url,
                    api_key=args.api_key,
                    device=args.device,
                    model=args.model,
                    language=args.language,
                    sample_rate=args.sample_rate,
                )
            )
        except KeyboardInterrupt:
            print("\n[Interrupted]")


if __name__ == "__main__":
    main()
