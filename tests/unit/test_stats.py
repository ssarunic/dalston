"""Unit tests for stats extraction module."""

from dalston.orchestrator.stats import JobResultStats, extract_stats_from_transcript


class TestExtractStatsFromTranscript:
    """Tests for extract_stats_from_transcript function."""

    def test_extracts_all_stats_from_complete_transcript(self):
        """Test extraction from a complete transcript with all fields."""
        transcript = {
            "job_id": "test-job-123",
            "version": "1.0",
            "metadata": {
                "audio_duration": 120.5,
                "language": "en",
                "language_confidence": 0.95,
                "speaker_count": 2,
            },
            "text": "Hello world this is a test transcript",
            "speakers": [
                {"id": "SPEAKER_00", "label": None},
                {"id": "SPEAKER_01", "label": None},
            ],
            "segments": [
                {"id": "seg_000", "start": 0.0, "end": 5.0, "text": "Hello world"},
                {
                    "id": "seg_001",
                    "start": 5.0,
                    "end": 10.0,
                    "text": "this is a test transcript",
                },
            ],
        }

        stats = extract_stats_from_transcript(transcript)

        assert stats.language_code == "en"
        assert stats.word_count == 7
        assert stats.segment_count == 2
        assert stats.speaker_count == 2
        assert stats.character_count == 37  # "Hello world this is a test transcript"

    def test_uses_metadata_speaker_count_over_speakers_array(self):
        """Test that metadata.speaker_count takes precedence over speakers array."""
        transcript = {
            "metadata": {
                "language": "fr",
                "speaker_count": 5,  # Different from speakers array length
            },
            "text": "Bonjour monde",
            "speakers": [{"id": "SPEAKER_00"}],  # Only 1 speaker in array
            "segments": [],
        }

        stats = extract_stats_from_transcript(transcript)

        assert stats.speaker_count == 5
        assert stats.language_code == "fr"

    def test_handles_empty_transcript(self):
        """Test extraction from empty/minimal transcript."""
        transcript = {
            "metadata": {},
            "text": "",
            "speakers": [],
            "segments": [],
        }

        stats = extract_stats_from_transcript(transcript)

        assert stats.language_code is None
        assert stats.word_count == 0
        assert stats.segment_count == 0
        assert stats.speaker_count is None  # None when no diarization
        assert stats.character_count == 0

    def test_handles_missing_metadata(self):
        """Test extraction when metadata is missing."""
        transcript = {
            "text": "Some text",
            "speakers": [],
            "segments": [{"id": "seg_000"}],
        }

        stats = extract_stats_from_transcript(transcript)

        assert stats.language_code is None
        assert stats.word_count == 2
        assert stats.segment_count == 1
        assert stats.speaker_count is None  # None when no speakers

    def test_handles_missing_text(self):
        """Test extraction when text is missing.

        Note: language_code is intentionally None when there's no text,
        as empty transcripts have unreliable language detection.
        """
        transcript = {
            "metadata": {"language": "de"},
            "speakers": [],
            "segments": [],
        }

        stats = extract_stats_from_transcript(transcript)

        # Language is None when no text (unreliable detection for empty transcripts)
        assert stats.language_code is None
        assert stats.word_count == 0
        assert stats.character_count == 0

    def test_word_count_handles_multiple_spaces(self):
        """Test that word count handles multiple spaces correctly."""
        transcript = {
            "metadata": {},
            "text": "  Hello    world   ",
            "speakers": [],
            "segments": [],
        }

        stats = extract_stats_from_transcript(transcript)

        # split() handles multiple spaces correctly
        assert stats.word_count == 2

    def test_character_count_strips_whitespace(self):
        """Test that character count strips leading/trailing whitespace."""
        transcript = {
            "metadata": {},
            "text": "   Hello world   ",
            "speakers": [],
            "segments": [],
        }

        stats = extract_stats_from_transcript(transcript)

        assert stats.character_count == 11  # "Hello world" without surrounding spaces

    def test_falls_back_to_speakers_array_length(self):
        """Test fallback to speakers array length when metadata.speaker_count is missing."""
        transcript = {
            "metadata": {"language": "es"},
            "text": "Hola",
            "speakers": [
                {"id": "SPEAKER_00"},
                {"id": "SPEAKER_01"},
                {"id": "SPEAKER_02"},
            ],
            "segments": [],
        }

        stats = extract_stats_from_transcript(transcript)

        assert stats.speaker_count == 3


class TestJobResultStats:
    """Tests for JobResultStats dataclass."""

    def test_dataclass_fields(self):
        """Test that JobResultStats has all expected fields."""
        stats = JobResultStats(
            language_code="en",
            word_count=100,
            segment_count=10,
            speaker_count=2,
            character_count=500,
        )

        assert stats.language_code == "en"
        assert stats.word_count == 100
        assert stats.segment_count == 10
        assert stats.speaker_count == 2
        assert stats.character_count == 500

    def test_allows_none_language_code(self):
        """Test that language_code can be None."""
        stats = JobResultStats(
            language_code=None,
            word_count=0,
            segment_count=0,
            speaker_count=None,
            character_count=0,
        )

        assert stats.language_code is None

    def test_allows_none_speaker_count(self):
        """Test that speaker_count can be None (no diarization)."""
        stats = JobResultStats(
            language_code="en",
            word_count=100,
            segment_count=10,
            speaker_count=None,
            character_count=500,
        )

        assert stats.speaker_count is None
