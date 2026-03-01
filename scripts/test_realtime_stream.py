#!/usr/bin/env python3
"""Stream audio file to OpenAI-compatible realtime transcription endpoint.

Simulates real-time streaming with pauses to mimic network slowness.
"""

import argparse
import asyncio
import base64
import json
import random
import wave
from pathlib import Path

import websockets

# Configuration
WS_URL = "ws://localhost:8000/v1/realtime"
API_KEY = "test-key"


async def resample_audio(input_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample audio using linear interpolation."""
    import struct

    # Unpack 16-bit samples
    samples = struct.unpack(f"<{len(input_data) // 2}h", input_data)

    # Calculate resampling ratio
    ratio = to_rate / from_rate

    # Resample using linear interpolation
    new_length = int(len(samples) * ratio)
    resampled = []
    for i in range(new_length):
        src_idx = i / ratio
        idx_low = int(src_idx)
        idx_high = min(idx_low + 1, len(samples) - 1)
        frac = src_idx - idx_low
        sample = int(samples[idx_low] * (1 - frac) + samples[idx_high] * frac)
        resampled.append(sample)

    # Pack back to bytes
    return struct.pack(f"<{len(resampled)}h", *resampled)


async def stream_audio(
    audio_path: Path,
    api_key: str = API_KEY,
    chunk_duration_ms: int = 100,
    pause_probability: float = 0.1,
    pause_min_ms: int = 500,
    pause_max_ms: int = 3000,
):
    """Stream audio file to realtime transcription endpoint."""
    # Read audio file
    print(f"Reading audio file: {audio_path}")
    with wave.open(str(audio_path), "rb") as wav:
        src_sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        n_frames = wav.getnframes()
        frames = wav.readframes(n_frames)

    print(f"  Source: {src_sample_rate}Hz, {channels}ch, {sample_width * 8}bit")
    print(f"  Duration: {n_frames / src_sample_rate:.1f}s")

    # Use source sample rate - worker handles resampling internally
    # Note: Silero VAD in workers only supports 8kHz/16kHz
    target_rate = src_sample_rate

    # Calculate chunk size (16-bit = 2 bytes per sample)
    bytes_per_sec = target_rate * 2
    chunk_size = int(bytes_per_sec * chunk_duration_ms / 1000)

    print(f"\nConnecting to {WS_URL}...")
    # Use query param for auth (more reliable with websockets library)
    url = f"{WS_URL}?intent=transcription&model=gpt-4o-transcribe&api_key={api_key}"

    try:
        async with websockets.connect(
            url,
            additional_headers={
                "OpenAI-Beta": "realtime=v1",
            },
            open_timeout=10,
        ) as ws:
            print("Connected!\n")

            # Receive session created
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            print(f"← {data['type']}")
            session_id = data.get("session", {}).get("id", "unknown")
            print(f"  Session: {session_id}\n")

            # Configure session - note: we send at source sample rate (16kHz)
            # The worker handles the audio format internally
            await ws.send(
                json.dumps(
                    {
                        "type": "transcription_session.update",
                        "session": {
                            "input_audio_transcription": {
                                "model": "gpt-4o-transcribe",
                            },
                            "turn_detection": {"type": "server_vad"},
                        },
                    }
                )
            )
            print("→ transcription_session.update")

            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            print(f"← {data['type']}\n")

            # Start receiver task
            receiver_task = asyncio.create_task(receive_events(ws))

            # Stream audio chunks
            print("Streaming audio...")
            print("-" * 60)

            total_chunks = (len(frames) + chunk_size - 1) // chunk_size
            chunks_sent = 0
            total_paused_ms = 0

            for i in range(0, len(frames), chunk_size):
                chunk = frames[i : i + chunk_size]
                audio_b64 = base64.b64encode(chunk).decode()

                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": audio_b64,
                        }
                    )
                )
                chunks_sent += 1

                # Progress indicator
                elapsed_ms = chunks_sent * chunk_duration_ms
                elapsed_s = elapsed_ms / 1000
                progress = chunks_sent / total_chunks * 100
                print(
                    f"\r  Sent: {elapsed_s:.1f}s / {len(frames) / bytes_per_sec:.1f}s ({progress:.0f}%)",
                    end="",
                    flush=True,
                )

                # Simulate network delay between chunks
                await asyncio.sleep(chunk_duration_ms / 1000)

                # Random pause to simulate network slowness
                if random.random() < pause_probability:
                    pause_ms = random.randint(pause_min_ms, pause_max_ms)
                    total_paused_ms += pause_ms
                    print(f"\n  [Pause {pause_ms}ms to simulate network...]", end="")
                    await asyncio.sleep(pause_ms / 1000)

            print(f"\n\nDone streaming! (paused {total_paused_ms}ms total)")

            # Commit buffer
            print("\n→ input_audio_buffer.commit")
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

            # Wait for remaining transcriptions
            print("\nWaiting for final transcriptions...")
            try:
                await asyncio.wait_for(receiver_task, timeout=15)
            except TimeoutError:
                print("(timeout)")
                receiver_task.cancel()

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"Connection rejected: HTTP {e.status_code}")
        if e.status_code == 401:
            print("Authentication failed - check API key")
        elif e.status_code == 503:
            print("No realtime workers available")
    except Exception as e:
        print(f"Error: {e}")
        raise


async def receive_events(ws):
    """Receive and display transcription events."""
    transcripts = []

    try:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            event_type = data.get("type", "unknown")

            if event_type == "input_audio_buffer.committed":
                print(f"\n← committed: {data.get('item_id')}")

            elif event_type == "input_audio_buffer.speech_started":
                print(f"\n← speech_started @ {data.get('audio_start_ms')}ms")

            elif event_type == "input_audio_buffer.speech_stopped":
                print(f"\n← speech_stopped @ {data.get('audio_end_ms')}ms")

            elif event_type == "conversation.item.input_audio_transcription.delta":
                delta = data.get("delta", "")
                print(f'\n← delta: "{delta}"')

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "")
                transcripts.append(transcript)
                print(f'\n← FINAL: "{transcript}"')
                print("-" * 60)

            elif event_type == "error":
                error = data.get("error", {})
                print(f"\n← ERROR: {error.get('code')}: {error.get('message')}")
                break

    except TimeoutError:
        pass

    if transcripts:
        print("\n" + "=" * 60)
        print("FULL TRANSCRIPT:")
        print("=" * 60)
        print(" ".join(transcripts))
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Stream audio to realtime transcription"
    )
    parser.add_argument("audio_file", type=Path, help="Path to WAV audio file")
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=100,
        help="Chunk duration in milliseconds (default: 100)",
    )
    parser.add_argument(
        "--pause-prob",
        type=float,
        default=0.1,
        help="Probability of random pause (default: 0.1)",
    )
    parser.add_argument(
        "--pause-min",
        type=int,
        default=500,
        help="Minimum pause duration in ms (default: 500)",
    )
    parser.add_argument(
        "--pause-max",
        type=int,
        default=3000,
        help="Maximum pause duration in ms (default: 3000)",
    )
    parser.add_argument(
        "--api-key",
        default=API_KEY,
        help="API key for authentication",
    )
    args = parser.parse_args()

    if not args.audio_file.exists():
        print(f"Error: Audio file not found: {args.audio_file}")
        return 1

    asyncio.run(
        stream_audio(
            args.audio_file,
            api_key=args.api_key,
            chunk_duration_ms=args.chunk_ms,
            pause_probability=args.pause_prob,
            pause_min_ms=args.pause_min,
            pause_max_ms=args.pause_max,
        )
    )


if __name__ == "__main__":
    main()
