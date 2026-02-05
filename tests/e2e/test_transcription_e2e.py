"""End-to-end transcription tests using the dalston CLI.

These tests require a running Docker stack and are excluded from the
default pytest run.  Execute with:

    pytest -m e2e -v
"""

import pytest

from tests.e2e.conftest import transcribe_json


@pytest.mark.e2e
class TestDefaultTranscription:
    """Default speaker_detection=none pipeline."""

    def test_mono_transcription(self, audio_dir):
        """Mono file produces top-level text and segments."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        assert result["text"]
        assert len(result["segments"]) > 0
        assert all(seg["text"] for seg in result["segments"])

    def test_segment_timestamps(self, audio_dir):
        """timestamps=segment skips word-level alignment."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--timestamps",
            "segment",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0
        assert not result.get("words")


@pytest.mark.e2e
class TestPerChannelTranscription:
    """per_channel speaker detection pipeline."""

    def test_stereo_per_channel(self, audio_dir):
        """Stereo file with per-channel produces two speakers."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--speakers",
            "per-channel",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0
        speakers = {s["id"] for s in result.get("speakers", [])}
        assert "SPEAKER_00" in speakers
        assert "SPEAKER_01" in speakers


@pytest.mark.e2e
class TestDiarizeTranscription:
    """Diarize speaker detection pipeline."""

    def test_mono_diarize(self, audio_dir):
        """Mono file with diarize completes with segments."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--speakers",
            "diarize",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0

    def test_stereo_diarize(self, audio_dir):
        """Stereo file with diarize completes with segments."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--speakers",
            "diarize",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0
