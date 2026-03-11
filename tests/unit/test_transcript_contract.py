"""Contract parity tests for DalstonTranscriptV1.

Validates that:
1. The schema can be instantiated with required fields
2. Model-specific fields land in metadata, not as top-level keys
3. Round-trip serialization preserves all data
4. Cross-runtime field coverage is consistent
5. DalstonTranscriptV1 can be consumed by transcript assembly
"""

import pytest
from pydantic import ValidationError

from dalston.common.pipeline_types import (
    AlignmentMethod,
    DalstonTranscriptV1,
    TimestampGranularity,
    TranscriptSegment,
    TranscriptWord,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_word(**overrides):
    defaults = {
        "text": "hello",
        "start": 0.0,
        "end": 0.5,
        "confidence": 0.95,
        "alignment_method": AlignmentMethod.ATTENTION,
    }
    defaults.update(overrides)
    return TranscriptWord(**defaults)


def _make_segment(**overrides):
    defaults = {
        "start": 0.0,
        "end": 2.0,
        "text": "hello world",
        "words": [_make_word(text="hello", start=0.0, end=0.5), _make_word(text="world", start=0.5, end=1.0)],
    }
    defaults.update(overrides)
    return TranscriptSegment(**defaults)


def _make_transcript(**overrides):
    defaults = {
        "text": "hello world",
        "segments": [_make_segment()],
        "language": "en",
        "runtime": "faster-whisper",
        "language_confidence": 0.98,
        "duration": 2.0,
        "timestamp_granularity": TimestampGranularity.WORD,
        "alignment_method": AlignmentMethod.ATTENTION,
    }
    defaults.update(overrides)
    return DalstonTranscriptV1(**defaults)


# ---------------------------------------------------------------------------
# Schema conformance tests
# ---------------------------------------------------------------------------


class TestSchemaConformance:
    """Test that DalstonTranscriptV1 validates required fields and rejects unknown ones."""

    def test_valid_minimal(self):
        """Minimal valid transcript with just required fields."""
        t = DalstonTranscriptV1(
            text="hello",
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
            language="en",
            runtime="test-engine",
        )
        assert t.schema_version == "1"
        assert t.text == "hello"
        assert t.language == "en"
        assert t.runtime == "test-engine"
        assert t.warnings == []
        assert t.metadata == {}

    def test_valid_full(self):
        """Full transcript with all optional fields."""
        t = _make_transcript()
        assert t.language_confidence == 0.98
        assert t.duration == 2.0
        assert t.timestamp_granularity == TimestampGranularity.WORD
        assert t.alignment_method == AlignmentMethod.ATTENTION

    def test_rejects_extra_fields(self):
        """StrictModel rejects unknown top-level fields."""
        with pytest.raises(ValidationError):
            DalstonTranscriptV1(
                text="hello",
                segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
                language="en",
                runtime="test",
                unknown_field="should fail",
            )

    def test_segment_rejects_extra_fields(self):
        """TranscriptSegment rejects unknown fields."""
        with pytest.raises(ValidationError):
            TranscriptSegment(
                start=0.0,
                end=1.0,
                text="hello",
                compression_ratio=1.5,  # Should be in metadata
            )

    def test_word_rejects_extra_fields(self):
        """TranscriptWord rejects unknown fields."""
        with pytest.raises(ValidationError):
            TranscriptWord(
                text="hello",
                start=0.0,
                end=0.5,
                logprob=-0.3,  # Should be in metadata
            )


# ---------------------------------------------------------------------------
# Metadata tests
# ---------------------------------------------------------------------------


class TestMetadataPlacement:
    """Test that model-specific data lives in metadata, not top-level."""

    def test_whisper_segment_metadata(self):
        """Whisper-specific fields go in segment metadata."""
        seg = TranscriptSegment(
            start=0.0,
            end=2.0,
            text="hello",
            metadata={
                "compression_ratio": 1.5,
                "no_speech_prob": 0.01,
                "avg_logprob": -0.28,
                "tokens": [1, 2, 3],
                "temperature": 0.0,
            },
        )
        assert seg.metadata["compression_ratio"] == 1.5
        assert seg.metadata["no_speech_prob"] == 0.01
        assert seg.metadata["avg_logprob"] == -0.28
        assert seg.metadata["tokens"] == [1, 2, 3]

    def test_parakeet_segment_metadata(self):
        """Parakeet-specific fields go in segment metadata."""
        seg = TranscriptSegment(
            start=0.0,
            end=2.0,
            text="hello",
            metadata={"decoder_type": "tdt"},
        )
        assert seg.metadata["decoder_type"] == "tdt"

    def test_word_metadata(self):
        """Word-level metadata is preserved."""
        word = TranscriptWord(
            text="hello",
            start=0.0,
            end=0.5,
            confidence=0.95,
            metadata={"logprob": -0.3},
        )
        assert word.metadata["logprob"] == -0.3

    def test_transcript_metadata(self):
        """Transcript-level metadata is preserved."""
        t = _make_transcript(metadata={"model_id": "large-v3-turbo"})
        assert t.metadata["model_id"] == "large-v3-turbo"


# ---------------------------------------------------------------------------
# Round-trip serialization tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Test JSON serialization round-trip preserves all data."""

    def test_round_trip_json(self):
        """Serialize to JSON and deserialize — no data loss."""
        original = _make_transcript(
            metadata={"model_id": "large-v3-turbo"},
            warnings=["low confidence"],
        )
        json_str = original.model_dump_json()
        restored = DalstonTranscriptV1.model_validate_json(json_str)

        assert restored.text == original.text
        assert restored.language == original.language
        assert restored.runtime == original.runtime
        assert restored.language_confidence == original.language_confidence
        assert restored.duration == original.duration
        assert restored.timestamp_granularity == original.timestamp_granularity
        assert restored.alignment_method == original.alignment_method
        assert restored.schema_version == original.schema_version
        assert restored.warnings == original.warnings
        assert restored.metadata == original.metadata
        assert len(restored.segments) == len(original.segments)

    def test_round_trip_dict(self):
        """Serialize to dict and deserialize — no data loss."""
        original = _make_transcript()
        data = original.model_dump(mode="json")
        restored = DalstonTranscriptV1.model_validate(data)

        assert restored == original

    def test_round_trip_with_metadata(self):
        """Metadata at all levels survives round-trip."""
        original = DalstonTranscriptV1(
            text="test",
            segments=[
                TranscriptSegment(
                    start=0.0,
                    end=1.0,
                    text="test",
                    words=[
                        TranscriptWord(
                            text="test",
                            start=0.0,
                            end=1.0,
                            metadata={"logprob": -0.5},
                        )
                    ],
                    metadata={"compression_ratio": 1.2},
                )
            ],
            language="en",
            runtime="test",
            metadata={"model": "test-model"},
        )

        data = original.model_dump(mode="json")
        restored = DalstonTranscriptV1.model_validate(data)

        assert restored.metadata["model"] == "test-model"
        assert restored.segments[0].metadata["compression_ratio"] == 1.2
        assert restored.segments[0].words[0].metadata["logprob"] == -0.5


# ---------------------------------------------------------------------------
# Cross-runtime field coverage tests
# ---------------------------------------------------------------------------


_RUNTIME_FIXTURES = {
    "faster-whisper": {
        "text": "hello world",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
                "words": [
                    {"text": "hello", "start": 0.0, "end": 0.5, "confidence": 0.95, "alignment_method": "attention"},
                    {"text": "world", "start": 0.5, "end": 1.0, "confidence": 0.90, "alignment_method": "attention"},
                ],
                "metadata": {
                    "compression_ratio": 1.5,
                    "no_speech_prob": 0.01,
                    "avg_logprob": -0.28,
                    "tokens": [1, 2, 3],
                    "temperature": 0.0,
                },
            }
        ],
        "language": "en",
        "language_confidence": 0.98,
        "duration": 2.0,
        "timestamp_granularity": "word",
        "alignment_method": "attention",
        "runtime": "faster-whisper",
    },
    "parakeet-onnx": {
        "text": "hello world",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
                "words": [
                    {"text": "hello", "start": 0.0, "end": 0.5, "confidence": 0.92, "alignment_method": "ctc"},
                    {"text": "world", "start": 0.5, "end": 1.0, "confidence": 0.88, "alignment_method": "ctc"},
                ],
                "metadata": {"decoder_type": "ctc"},
            }
        ],
        "language": "en",
        "language_confidence": 1.0,
        "timestamp_granularity": "word",
        "alignment_method": "ctc",
        "runtime": "nemo-onnx",
    },
    "voxtral": {
        "text": "hello world",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
            }
        ],
        "language": "en",
        "timestamp_granularity": "segment",
        "alignment_method": "unknown",
        "runtime": "voxtral",
    },
}


class TestCrossRuntimeCoverage:
    """Test that fixtures from each runtime validate against the schema."""

    @pytest.mark.parametrize("runtime", list(_RUNTIME_FIXTURES.keys()))
    def test_runtime_fixture_validates(self, runtime):
        """Each runtime fixture validates as DalstonTranscriptV1."""
        data = _RUNTIME_FIXTURES[runtime]
        transcript = DalstonTranscriptV1.model_validate(data)

        # Required fields always present
        assert transcript.text
        assert transcript.language
        assert transcript.runtime
        assert len(transcript.segments) > 0

    @pytest.mark.parametrize("runtime", list(_RUNTIME_FIXTURES.keys()))
    def test_runtime_granularity_populated(self, runtime):
        """Each runtime populates timestamp_granularity."""
        data = _RUNTIME_FIXTURES[runtime]
        transcript = DalstonTranscriptV1.model_validate(data)
        assert transcript.timestamp_granularity in TimestampGranularity

    @pytest.mark.parametrize("runtime", list(_RUNTIME_FIXTURES.keys()))
    def test_runtime_alignment_method_populated(self, runtime):
        """Each runtime populates alignment_method."""
        data = _RUNTIME_FIXTURES[runtime]
        transcript = DalstonTranscriptV1.model_validate(data)
        assert transcript.alignment_method in AlignmentMethod

    @pytest.mark.parametrize("runtime", list(_RUNTIME_FIXTURES.keys()))
    def test_model_specific_fields_in_metadata(self, runtime):
        """Model-specific fields are in segment/word metadata, not top-level."""
        data = _RUNTIME_FIXTURES[runtime]
        transcript = DalstonTranscriptV1.model_validate(data)

        for seg in transcript.segments:
            # These should NOT be top-level segment fields
            assert not hasattr(seg, "compression_ratio")
            assert not hasattr(seg, "no_speech_prob")
            assert not hasattr(seg, "avg_logprob")
            assert not hasattr(seg, "tokens")
            assert not hasattr(seg, "temperature")


# ---------------------------------------------------------------------------
# Transcript assembly integration tests
# ---------------------------------------------------------------------------


class TestTranscriptAssembly:
    """Test that DalstonTranscriptV1 integrates with transcript assembly."""

    def test_assemble_with_v1_output(self):
        """Transcript assembly works with DalstonTranscriptV1 stage output."""
        from dalston.common.transcript import assemble_transcript

        transcript_data = _RUNTIME_FIXTURES["faster-whisper"]
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {"duration": 2.0, "channels": 1, "sample_rate": 16000, "artifact_id": "a1", "format": "wav"}
                ],
                "runtime": "ffmpeg",
            },
            "transcribe": transcript_data,
        }

        result = assemble_transcript(
            job_id="test-job",
            stage_outputs=stage_outputs,
        )

        assert result.text
        assert len(result.segments) > 0
        assert result.metadata.language == "en"

    def test_assembler_add_transcript(self):
        """TranscriptAssembler.add_transcript works with DalstonTranscriptV1."""
        from dalston.realtime_sdk.assembler import TranscriptAssembler

        assembler = TranscriptAssembler()
        transcript = _make_transcript()

        segment = assembler.add_transcript(transcript, audio_duration=2.0)

        assert segment.text == "hello world"
        assert segment.start == 0.0
        assert segment.end == 2.0
        assert len(segment.words) == 2
