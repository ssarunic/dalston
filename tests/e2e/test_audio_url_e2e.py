"""End-to-end tests for audio URL transcription.

These tests require a running Docker stack and are excluded from the
default pytest run.  Execute with:

    pytest -m e2e -v tests/e2e/test_audio_url_e2e.py

These tests also require internet access to download audio from URLs.
"""

import json
import subprocess

import pytest

# Google Drive test file URL (publicly shared audio file)
# This should be a short audio clip for fast testing
GOOGLE_DRIVE_URL = "https://drive.google.com/file/d/1jZrK5n_wwInJ5AAf3xrJSjDBHNUSaPOX/view?usp=share_link"


def run_dalston(*args, timeout=300):
    """Run the ``dalston`` CLI and return the CompletedProcess."""
    return subprocess.run(
        ["dalston", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def transcribe_url_json(url, *extra_args, timeout=300):
    """Run ``dalston transcribe --url <url> --format json --wait`` and return parsed output."""
    result = run_dalston(
        "transcribe",
        "--url",
        url,
        "--format",
        "json",
        "--wait",
        *extra_args,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"CLI exited with code {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return json.loads(result.stdout, strict=False)


@pytest.mark.e2e
class TestAudioUrlTranscription:
    """Tests for transcription from URL."""

    def test_transcribe_from_google_drive_url(self):
        """Transcribe audio from Google Drive URL produces valid output."""
        result = transcribe_url_json(GOOGLE_DRIVE_URL)

        assert result["status"] == "completed"
        assert result["text"]
        assert len(result["segments"]) > 0
        assert all(seg["text"] for seg in result["segments"])

    def test_transcribe_from_url_with_diarization(self):
        """Transcribe from URL with diarization enabled."""
        result = transcribe_url_json(
            GOOGLE_DRIVE_URL,
            "--speakers",
            "diarize",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0

    def test_transcribe_from_url_segment_timestamps(self):
        """Transcribe from URL with segment-level timestamps only."""
        result = transcribe_url_json(
            GOOGLE_DRIVE_URL,
            "--timestamps",
            "segment",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0
        # segment-level timestamps skip word alignment
        assert not result.get("words")


@pytest.mark.e2e
class TestAudioUrlErrors:
    """Tests for error handling with audio URLs."""

    def test_invalid_url_format(self):
        """Invalid URL format produces error."""
        result = run_dalston(
            "transcribe",
            "--url",
            "not-a-valid-url",
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0
        assert "Error" in result.stderr or "error" in result.stderr.lower()

    def test_url_not_found(self):
        """Non-existent URL produces error."""
        result = run_dalston(
            "transcribe",
            "--url",
            "https://example.com/nonexistent-audio-file-12345.mp3",
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0

    def test_both_file_and_url_rejected(self, audio_dir):
        """Providing both file and URL produces error."""
        from tests.e2e.conftest import AUDIO_DIR

        result = run_dalston(
            "transcribe",
            str(AUDIO_DIR / "test_merged.wav"),
            "--url",
            GOOGLE_DRIVE_URL,
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0
        assert "not both" in result.stderr.lower() or "Error" in result.stderr

    def test_no_file_or_url_rejected(self):
        """Providing neither file nor URL produces error."""
        result = run_dalston(
            "transcribe",
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0
