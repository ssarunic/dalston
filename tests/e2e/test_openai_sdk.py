"""E2E test using the official OpenAI Python SDK against Dalston's OpenAI-compatible API."""

import os
from pathlib import Path

from openai import OpenAI


def test_transcription_with_openai_sdk():
    """Test transcription using the official OpenAI Python SDK."""
    # Configure client to point to Dalston
    client = OpenAI(
        api_key=os.getenv(
            "DALSTON_API_KEY", "dk_ZBPh23OgSotMC2jHyzDYefh-OohR_Zpgkig0-PxpW9Y"
        ),
        base_url="http://localhost:8000/v1",
    )

    # Use a test audio file
    audio_file = Path("/Users/sasasarunic/Downloads/audio-2.wav")
    if not audio_file.exists():
        audio_file = Path(__file__).parent.parent / "fixtures" / "test_audio.wav"

    print(f"Using audio file: {audio_file}")

    # Test 1: Simple transcription (json format)
    print("\n=== Test 1: Simple JSON transcription ===")
    with open(audio_file, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
        )
    print(f"Result type: {type(result)}")
    print(f"Text (first 200 chars): {result.text[:200]}...")
    assert hasattr(result, "text")
    assert len(result.text) > 0

    # Test 2: Verbose JSON with segments
    print("\n=== Test 2: Verbose JSON with segments ===")
    with open(audio_file, "rb") as f:
        result = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f,
            response_format="verbose_json",
        )
    print(f"Task: {result.task}")
    print(f"Language: {result.language}")
    print(f"Duration: {result.duration}")
    print(f"Segments count: {len(result.segments)}")
    print(f"First segment: {result.segments[0].text if result.segments else 'N/A'}")
    assert result.task == "transcribe"
    assert len(result.segments) > 0

    # Test 3: Text format (plain text)
    print("\n=== Test 3: Plain text format ===")
    with open(audio_file, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )
    # Text format returns a string directly
    print(f"Result type: {type(result)}")
    print(f"Text (first 200 chars): {str(result)[:200]}...")

    print("\n=== All OpenAI SDK tests passed! ===")


if __name__ == "__main__":
    test_transcription_with_openai_sdk()
