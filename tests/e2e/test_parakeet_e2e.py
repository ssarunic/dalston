"""End-to-end Parakeet transcription tests using the dalston CLI.

These tests require a running Docker stack with the Parakeet engine
and are excluded from the default pytest run.  Execute with:

    pytest -m e2e -v tests/e2e/test_parakeet_e2e.py

For CPU-only testing (slower but works on Mac):
    docker compose --profile parakeet-cpu up -d
    pytest -m e2e -v tests/e2e/test_parakeet_e2e.py
"""

import pytest

from tests.e2e.conftest import transcribe_json


@pytest.mark.e2e
class TestParakeetTranscription:
    """Parakeet transcription tests (English-only, native word timestamps)."""

    def test_parakeet_mono_transcription(self, audio_dir):
        """Parakeet transcribes mono file with word timestamps."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--model",
            "parakeet-0.6b",
            timeout=300,  # Allow extra time for model loading
        )

        assert result["status"] == "completed"
        assert result["text"]
        assert len(result["segments"]) > 0
        assert all(seg["text"] for seg in result["segments"])

    def test_parakeet_produces_word_timestamps(self, audio_dir):
        """Parakeet produces native word-level timestamps without alignment."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--model",
            "parakeet-0.6b",
            "--timestamps",
            "word",
            timeout=300,
        )

        assert result["status"] == "completed"
        # Parakeet produces native word timestamps (stored in segments)
        segments_with_words = [s for s in result["segments"] if s.get("words")]
        assert len(segments_with_words) > 0, (
            "Parakeet should produce word-level timestamps"
        )

        # Verify word structure
        first_words = segments_with_words[0]["words"]
        for word in first_words:
            assert "word" in word or "text" in word
            assert "start" in word
            assert "end" in word

    def test_parakeet_segment_timestamps(self, audio_dir):
        """Parakeet with segment-level timestamps (no word alignment)."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--model",
            "parakeet-0.6b",
            "--timestamps",
            "segment",
            timeout=300,
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0

        # Verify segment structure
        for seg in result["segments"]:
            assert "text" in seg
            assert "start" in seg
            assert "end" in seg

    def test_parakeet_with_diarization(self, audio_dir):
        """Parakeet with diarization for speaker identification."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--model",
            "parakeet-0.6b",
            "--speakers",
            "diarize",
            timeout=300,
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0

        # Segments should have speaker labels after diarization
        speakers = {s["id"] for s in result.get("speakers", [])}
        assert len(speakers) > 0, "Diarization should identify speakers"

    def test_parakeet_alias_works(self, audio_dir):
        """The 'parakeet' model alias works correctly."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--model",
            "parakeet",  # Alias for parakeet-0.6b
            timeout=300,
        )

        assert result["status"] == "completed"
        assert result["text"]
