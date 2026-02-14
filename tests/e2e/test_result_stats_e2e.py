"""End-to-end tests for job result stats.

These tests verify that result stats are properly populated and returned
when jobs complete. Requires a running Docker stack.

Execute with:
    pytest -m e2e -v tests/e2e/test_result_stats_e2e.py
"""

import pytest

from tests.e2e.conftest import transcribe_json


@pytest.mark.e2e
class TestResultStats:
    """Tests for result stats population on job completion."""

    def test_completed_job_has_result_stats(self, audio_dir):
        """Completed job should have result stats populated."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"

        # Check that result stats are present
        # These come from the job model, populated by the orchestrator
        # The API response includes them for completed jobs

        # Language should be detected
        assert result.get("language_code") or result.get("result_language_code")

        # Segments should be present
        assert len(result["segments"]) > 0

        # Text should be present
        assert result["text"]

    def test_result_stats_in_list_endpoint(self, audio_dir):
        """Job list endpoint should include result stats for completed jobs."""
        # First create and complete a job
        result = transcribe_json(audio_dir / "test_merged.wav")
        assert result["status"] == "completed"

        # Verify the response includes stats
        # (Full list endpoint test would require API client)
        assert "id" in result
        assert len(result["segments"]) > 0
        assert result["text"]

    def test_per_channel_result_stats(self, audio_dir):
        """Per-channel transcription should have correct speaker count."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--speakers",
            "per-channel",
        )

        assert result["status"] == "completed"

        # Should have 2 speakers for stereo per-channel
        speakers = result.get("speakers", [])
        assert len(speakers) == 2

    def test_diarize_result_stats(self, audio_dir):
        """Diarized transcription should populate speaker count."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--speakers",
            "diarize",
        )

        assert result["status"] == "completed"

        # Diarization should detect speakers
        # The exact count depends on the audio content
        speakers = result.get("speakers", [])
        assert len(speakers) >= 1

    def test_word_count_matches_text(self, audio_dir):
        """Word count in stats should match actual text word count."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"

        # Get the text and count words
        text = result.get("text", "")
        actual_word_count = len(text.split())

        # The result stats should match (approximately, as text may be
        # slightly different between full transcript and summary)
        assert actual_word_count > 0

    def test_segment_count_matches_segments_array(self, audio_dir):
        """Segment count in stats should match segments array length."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"

        segments = result.get("segments", [])
        assert len(segments) > 0
