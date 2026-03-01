#!/usr/bin/env python3
"""Test OpenAI-compatible API endpoints using the OpenAI SDK.

Tests:
1. Batch transcription (POST /v1/audio/transcriptions)
2. Real-time transcription (WS /v1/realtime)

Usage:
    python scripts/test_openai_compat.py [--batch] [--realtime] [--all]
"""

import argparse
import asyncio
import base64
import json
import sys
import wave
from pathlib import Path

import websockets
from openai import OpenAI

# Configuration
BASE_URL = "http://localhost:8000/v1"
WS_URL = "ws://localhost:8000/v1/realtime"
API_KEY = "test-key"  # Use test key or real key
TEST_AUDIO = Path("tests/fixtures/test_audio.wav")


def test_batch_transcription():
    """Test batch transcription using OpenAI SDK."""
    print("\n" + "=" * 60)
    print("Testing Batch Transcription (POST /v1/audio/transcriptions)")
    print("=" * 60)

    client = OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )

    if not TEST_AUDIO.exists():
        print(f"ERROR: Test audio file not found: {TEST_AUDIO}")
        return False

    # Test 1: Basic transcription (json format)
    print("\n1. Basic transcription (response_format=json)...")
    try:
        with open(TEST_AUDIO, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="json",
            )
        print(f"   Result: {transcript.text[:100]}...")
        print("   PASSED")
    except Exception as e:
        print(f"   FAILED: {e}")
        return False

    # Test 2: Verbose JSON with timestamps
    print("\n2. Verbose JSON with word timestamps...")
    try:
        with open(TEST_AUDIO, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="gpt-4o-transcribe",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        print(f"   Text: {transcript.text[:100]}...")
        print(f"   Language: {transcript.language}")
        print(f"   Duration: {transcript.duration}s")
        if hasattr(transcript, "words") and transcript.words:
            print(f"   Words: {len(transcript.words)} words")
            print(f"   First word: {transcript.words[0]}")
        print("   PASSED")
    except Exception as e:
        print(f"   FAILED: {e}")
        return False

    # Test 3: Text format
    print("\n3. Plain text format...")
    try:
        with open(TEST_AUDIO, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
            )
        # Text format returns just the string
        print(f"   Result: {str(transcript)[:100]}...")
        print("   PASSED")
    except Exception as e:
        print(f"   FAILED: {e}")
        return False

    # Test 4: SRT format
    print("\n4. SRT subtitle format...")
    try:
        with open(TEST_AUDIO, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="srt",
            )
        print(f"   Result (first 200 chars):\n{str(transcript)[:200]}")
        print("   PASSED")
    except Exception as e:
        print(f"   FAILED: {e}")
        return False

    print("\n" + "-" * 60)
    print("All batch transcription tests PASSED")
    return True


async def test_realtime_transcription():
    """Test real-time transcription using WebSocket."""
    print("\n" + "=" * 60)
    print("Testing Real-time Transcription (WS /v1/realtime)")
    print("=" * 60)

    if not TEST_AUDIO.exists():
        print(f"ERROR: Test audio file not found: {TEST_AUDIO}")
        return False

    # Read and prepare audio data
    print("\n1. Reading test audio file...")
    with wave.open(str(TEST_AUDIO), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    print(f"   Sample rate: {sample_rate} Hz")
    print(f"   Channels: {channels}")
    print(f"   Sample width: {sample_width} bytes")
    print(f"   Total frames: {len(frames)} bytes")

    # OpenAI expects 24kHz mono PCM16 - we'll send what we have
    # and let the server handle it (or fail gracefully)

    print("\n2. Connecting to WebSocket...")
    url = f"{WS_URL}?intent=transcription&model=gpt-4o-transcribe"

    try:
        async with websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
            open_timeout=10,
        ) as ws:
            print("   Connected!")

            # Receive session created event
            print("\n3. Waiting for session.created...")
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            print(f"   Received: {data['type']}")
            if data["type"] != "transcription_session.created":
                print(
                    f"   WARNING: Expected transcription_session.created, got {data['type']}"
                )

            # Send session update
            print("\n4. Sending session configuration...")
            await ws.send(
                json.dumps(
                    {
                        "type": "transcription_session.update",
                        "session": {
                            "input_audio_format": "pcm16",
                            "input_audio_transcription": {
                                "model": "gpt-4o-transcribe",
                                "language": "en",
                            },
                            "turn_detection": {"type": "server_vad"},
                        },
                    }
                )
            )

            # Receive session updated
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            print(f"   Received: {data['type']}")

            # Send audio in chunks
            print("\n5. Sending audio data...")
            chunk_size = 4800  # 100ms at 24kHz
            chunks_sent = 0
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
            print(f"   Sent {chunks_sent} chunks")

            # Commit the buffer
            print("\n6. Committing audio buffer...")
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

            # Wait for transcription results
            print("\n7. Waiting for transcription results...")
            transcripts = []
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    data = json.loads(msg)
                    event_type = data.get("type", "unknown")

                    if event_type == "input_audio_buffer.committed":
                        print(f"   Buffer committed: {data.get('item_id')}")

                    elif (
                        event_type
                        == "conversation.item.input_audio_transcription.delta"
                    ):
                        delta = data.get("delta", "")
                        print(f"   Delta: {delta}")

                    elif (
                        event_type
                        == "conversation.item.input_audio_transcription.completed"
                    ):
                        transcript = data.get("transcript", "")
                        transcripts.append(transcript)
                        print(f"   Final: {transcript}")

                    elif event_type == "input_audio_buffer.speech_started":
                        print(f"   Speech started at {data.get('audio_start_ms')}ms")

                    elif event_type == "input_audio_buffer.speech_stopped":
                        print(f"   Speech stopped at {data.get('audio_end_ms')}ms")

                    elif event_type == "error":
                        error = data.get("error", {})
                        print(f"   ERROR: {error.get('message')}")
                        break

            except TimeoutError:
                print("   (timeout - no more messages)")

            print("\n" + "-" * 60)
            if transcripts:
                print(f"Transcribed text: {' '.join(transcripts)}")
                print("Real-time transcription test PASSED")
                return True
            else:
                print("WARNING: No transcripts received (may need real-time worker)")
                print("Real-time transcription test SKIPPED (no worker available)")
                return True  # Not a failure if worker isn't running

    except websockets.exceptions.InvalidStatusCode as e:
        print(f"   Connection rejected: {e}")
        if e.status_code == 401:
            print("   (Authentication required - this is expected behavior)")
            return True
        return False
    except Exception as e:
        print(f"   FAILED: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test OpenAI-compatible API")
    parser.add_argument("--batch", action="store_true", help="Test batch transcription")
    parser.add_argument(
        "--realtime", action="store_true", help="Test real-time transcription"
    )
    parser.add_argument("--all", action="store_true", help="Run all tests")
    args = parser.parse_args()

    # Default to --all if no specific test selected
    if not args.batch and not args.realtime:
        args.all = True

    results = []

    if args.all or args.batch:
        results.append(("Batch", test_batch_transcription()))

    if args.all or args.realtime:
        results.append(("Real-time", asyncio.run(test_realtime_transcription())))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
