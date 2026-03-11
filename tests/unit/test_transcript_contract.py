"""Contract parity tests for Transcript.

Validates that:
1. The schema can be instantiated with required fields
2. Model-specific fields land in metadata, not as top-level keys
3. Round-trip serialization preserves all data
4. Cross-engine_id field coverage is consistent
5. Transcript can be consumed by transcript assembly
"""

import pytest
from pydantic import ValidationError

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Character,
    Phoneme,
    SegmentMetaKeys,
    TimestampGranularity,
    Transcript,
    TranscriptMetaKeys,
    TranscriptSegment,
    TranscriptWord,
    WordMetaKeys,
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
        "words": [
            _make_word(text="hello", start=0.0, end=0.5),
            _make_word(text="world", start=0.5, end=1.0),
        ],
    }
    defaults.update(overrides)
    return TranscriptSegment(**defaults)


def _make_transcript(**overrides):
    defaults = {
        "text": "hello world",
        "segments": [_make_segment()],
        "language": "en",
        "engine_id": "faster-whisper",
        "language_confidence": 0.98,
        "duration": 2.0,
        "timestamp_granularity": TimestampGranularity.WORD,
        "alignment_method": AlignmentMethod.ATTENTION,
    }
    defaults.update(overrides)
    return Transcript(**defaults)


# ---------------------------------------------------------------------------
# Schema conformance tests
# ---------------------------------------------------------------------------


class TestSchemaConformance:
    """Test that Transcript validates required fields and rejects unknown ones."""

    def test_valid_minimal(self):
        """Minimal valid transcript with just required fields."""
        t = Transcript(
            text="hello",
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
            language="en",
            engine_id="test-engine",
        )
        assert t.schema_version == "1"
        assert t.text == "hello"
        assert t.language == "en"
        assert t.engine_id == "test-engine"
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
            Transcript(
                text="hello",
                segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
                language="en",
                engine_id="test",
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
# Restored typed fields tests
# ---------------------------------------------------------------------------


class TestRestoredTypedFields:
    """Test fields restored from old Segment/Word schemas."""

    def test_word_with_characters(self):
        """TranscriptWord accepts typed Character list."""
        word = TranscriptWord(
            text="hello",
            start=0.0,
            end=0.5,
            characters=[
                Character(char="h", start=0.0, end=0.1),
                Character(char="e", start=0.1, end=0.2, confidence=0.9),
            ],
        )
        assert len(word.characters) == 2
        assert word.characters[0].char == "h"
        assert word.characters[1].confidence == 0.9

    def test_word_with_phonemes(self):
        """TranscriptWord accepts typed Phoneme list."""
        word = TranscriptWord(
            text="hello",
            start=0.0,
            end=0.5,
            phonemes=[
                Phoneme(phoneme="h", start=0.0, end=0.1),
                Phoneme(phoneme="ɛ", start=0.1, end=0.2, stress=1),
            ],
        )
        assert len(word.phonemes) == 2
        assert word.phonemes[1].stress == 1

    def test_segment_id(self):
        """TranscriptSegment accepts stable ID for incremental updates."""
        seg = TranscriptSegment(
            id="seg-001",
            start=0.0,
            end=2.0,
            text="hello",
        )
        assert seg.id == "seg-001"

    def test_segment_is_final(self):
        """TranscriptSegment tracks interim vs final results."""
        interim = TranscriptSegment(start=0.0, end=1.0, text="hel", is_final=False)
        final = TranscriptSegment(start=0.0, end=1.0, text="hello", is_final=True)
        assert interim.is_final is False
        assert final.is_final is True

    def test_segment_is_speech(self):
        """TranscriptSegment tracks speech vs non-speech."""
        speech = TranscriptSegment(start=0.0, end=1.0, text="hello", is_speech=True)
        music = TranscriptSegment(start=0.0, end=1.0, text="[music]", is_speech=False)
        assert speech.is_speech is True
        assert music.is_speech is False

    def test_defaults_are_none(self):
        """New optional fields default to None."""
        word = _make_word()
        seg = _make_segment()
        assert word.characters is None
        assert word.phonemes is None
        assert seg.id is None
        assert seg.is_final is None
        assert seg.is_speech is None


class TestMetaKeyConstants:
    """Test that metadata key constants match expected values."""

    def test_segment_meta_keys(self):
        assert SegmentMetaKeys.TOKENS == "tokens"
        assert SegmentMetaKeys.AVG_LOGPROB == "avg_logprob"
        assert SegmentMetaKeys.COMPRESSION_RATIO == "compression_ratio"
        assert SegmentMetaKeys.NO_SPEECH_PROB == "no_speech_prob"
        assert SegmentMetaKeys.TEMPERATURE == "temperature"
        assert SegmentMetaKeys.DECODER_TYPE == "decoder_type"

    def test_word_meta_keys(self):
        assert WordMetaKeys.LOGPROB == "logprob"

    def test_transcript_meta_keys(self):
        assert TranscriptMetaKeys.MODEL_ID == "model_id"

    def test_keys_match_fixture_data(self):
        """Constants match the keys used in engine_id fixtures."""
        whisper_meta = _RUNTIME_FIXTURES["faster-whisper"]["segments"][0]["metadata"]
        assert SegmentMetaKeys.COMPRESSION_RATIO in whisper_meta
        assert SegmentMetaKeys.NO_SPEECH_PROB in whisper_meta
        assert SegmentMetaKeys.AVG_LOGPROB in whisper_meta
        assert SegmentMetaKeys.TOKENS in whisper_meta
        assert SegmentMetaKeys.TEMPERATURE in whisper_meta


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
        restored = Transcript.model_validate_json(json_str)

        assert restored.text == original.text
        assert restored.language == original.language
        assert restored.engine_id == original.engine_id
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
        restored = Transcript.model_validate(data)

        assert restored == original

    def test_round_trip_with_metadata(self):
        """Metadata at all levels survives round-trip."""
        original = Transcript(
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
            engine_id="test",
            metadata={"model": "test-model"},
        )

        data = original.model_dump(mode="json")
        restored = Transcript.model_validate(data)

        assert restored.metadata["model"] == "test-model"
        assert restored.segments[0].metadata["compression_ratio"] == 1.2
        assert restored.segments[0].words[0].metadata["logprob"] == -0.5

    def test_round_trip_with_restored_fields(self):
        """Characters, phonemes, segment id/is_final/is_speech survive round-trip."""
        original = Transcript(
            text="test",
            segments=[
                TranscriptSegment(
                    id="seg-1",
                    start=0.0,
                    end=1.0,
                    text="test",
                    is_final=True,
                    is_speech=True,
                    words=[
                        TranscriptWord(
                            text="test",
                            start=0.0,
                            end=1.0,
                            characters=[
                                Character(char="t", start=0.0, end=0.25),
                                Character(char="e", start=0.25, end=0.5),
                            ],
                            phonemes=[
                                Phoneme(phoneme="t", start=0.0, end=0.25),
                                Phoneme(phoneme="ɛ", start=0.25, end=0.5, stress=1),
                            ],
                        )
                    ],
                )
            ],
            language="en",
            engine_id="test",
        )

        data = original.model_dump(mode="json")
        restored = Transcript.model_validate(data)

        seg = restored.segments[0]
        assert seg.id == "seg-1"
        assert seg.is_final is True
        assert seg.is_speech is True

        word = seg.words[0]
        assert len(word.characters) == 2
        assert word.characters[0].char == "t"
        assert len(word.phonemes) == 2
        assert word.phonemes[1].stress == 1


# ---------------------------------------------------------------------------
# Cross-engine_id field coverage tests
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
                    {
                        "text": "hello",
                        "start": 0.0,
                        "end": 0.5,
                        "confidence": 0.95,
                        "alignment_method": "attention",
                    },
                    {
                        "text": "world",
                        "start": 0.5,
                        "end": 1.0,
                        "confidence": 0.90,
                        "alignment_method": "attention",
                    },
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
        "engine_id": "faster-whisper",
    },
    "parakeet-onnx": {
        "text": "hello world",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world",
                "words": [
                    {
                        "text": "hello",
                        "start": 0.0,
                        "end": 0.5,
                        "confidence": 0.92,
                        "alignment_method": "ctc",
                    },
                    {
                        "text": "world",
                        "start": 0.5,
                        "end": 1.0,
                        "confidence": 0.88,
                        "alignment_method": "ctc",
                    },
                ],
                "metadata": {"decoder_type": "ctc"},
            }
        ],
        "language": "en",
        "language_confidence": 1.0,
        "timestamp_granularity": "word",
        "alignment_method": "ctc",
        "engine_id": "nemo-onnx",
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
        "engine_id": "voxtral",
    },
}


class TestCrossRuntimeCoverage:
    """Test that fixtures from each engine_id validate against the schema."""

    @pytest.mark.parametrize("engine_id", list(_RUNTIME_FIXTURES.keys()))
    def test_engine_id_fixture_validates(self, engine_id):
        """Each engine_id fixture validates as Transcript."""
        data = _RUNTIME_FIXTURES[engine_id]
        transcript = Transcript.model_validate(data)

        # Required fields always present
        assert transcript.text
        assert transcript.language
        assert transcript.engine_id
        assert len(transcript.segments) > 0

    @pytest.mark.parametrize("engine_id", list(_RUNTIME_FIXTURES.keys()))
    def test_engine_id_granularity_populated(self, engine_id):
        """Each engine_id populates timestamp_granularity."""
        data = _RUNTIME_FIXTURES[engine_id]
        transcript = Transcript.model_validate(data)
        assert transcript.timestamp_granularity in TimestampGranularity

    @pytest.mark.parametrize("engine_id", list(_RUNTIME_FIXTURES.keys()))
    def test_engine_id_alignment_method_populated(self, engine_id):
        """Each engine_id populates alignment_method."""
        data = _RUNTIME_FIXTURES[engine_id]
        transcript = Transcript.model_validate(data)
        assert transcript.alignment_method in AlignmentMethod

    @pytest.mark.parametrize("engine_id", list(_RUNTIME_FIXTURES.keys()))
    def test_model_specific_fields_in_metadata(self, engine_id):
        """Model-specific fields are in segment/word metadata, not top-level."""
        data = _RUNTIME_FIXTURES[engine_id]
        transcript = Transcript.model_validate(data)

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
    """Test that Transcript integrates with transcript assembly."""

    def test_assemble_with_v1_output(self):
        """Transcript assembly works with Transcript stage output."""
        from dalston.common.transcript import assemble_transcript

        transcript_data = _RUNTIME_FIXTURES["faster-whisper"]
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "duration": 2.0,
                        "channels": 1,
                        "sample_rate": 16000,
                        "artifact_id": "a1",
                        "format": "wav",
                    }
                ],
                "engine_id": "ffmpeg",
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
        """TranscriptAssembler.add_transcript works with Transcript."""
        from dalston.realtime_sdk.assembler import TranscriptAssembler

        assembler = TranscriptAssembler()
        transcript = _make_transcript()

        segment = assembler.add_transcript(transcript, audio_duration=2.0)

        assert segment.text == "hello world"
        assert segment.start == 0.0
        assert segment.end == 2.0
        assert len(segment.words) == 2

    def test_assemble_with_code_switching(self):
        """Transcript assembly propagates code-switching language data."""
        from dalston.common.transcript import assemble_transcript

        transcript_data = {
            "text": "Hello, comment allez-vous?",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "Hello,",
                    "language": "en",
                    "language_confidence": 0.95,
                },
                {
                    "start": 1.0,
                    "end": 3.0,
                    "text": "comment allez-vous?",
                    "language": "fr",
                    "language_confidence": 0.92,
                },
            ],
            "language": "en",
            "language_confidence": 0.7,
            "languages": [
                {"code": "en", "confidence": 0.7, "is_primary": True},
                {"code": "fr", "confidence": 0.3},
            ],
            "engine_id": "faster-whisper",
            "timestamp_granularity": "segment",
            "alignment_method": "attention",
        }
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "duration": 3.0,
                        "channels": 1,
                        "sample_rate": 16000,
                        "artifact_id": "a1",
                        "format": "wav",
                    }
                ],
                "engine_id": "ffmpeg",
            },
            "transcribe": transcript_data,
        }

        result = assemble_transcript(
            job_id="test-cs-job",
            stage_outputs=stage_outputs,
        )

        assert result.metadata.language == "en"
        assert result.metadata.languages is not None
        assert len(result.metadata.languages) == 2
        assert result.metadata.languages[0].code == "en"
        assert result.metadata.languages[0].is_primary is True
        assert result.metadata.languages[1].code == "fr"
        assert result.segments[0].language == "en"
        assert result.segments[1].language == "fr"
