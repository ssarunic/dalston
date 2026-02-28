"""Integration tests for engine typed outputs.

Tests that engine outputs conform to the strongly typed pipeline interfaces.
"""

from unittest.mock import patch

from dalston.common.pipeline_types import (
    AlignmentMethod,
    AlignOutput,
    AudioMedia,
    DiarizeOutput,
    MergeOutput,
    PrepareOutput,
    Segment,
    SpeakerDetectionMode,
    SpeakerTurn,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)
from dalston.engine_sdk.types import TaskInput, TaskOutput


class TestFinalMergerEngineOutput:
    """Tests for Final Merger engine typed output.

    This engine has no external ML dependencies, so we can test it directly.
    """

    def test_merge_output_structure(self, tmp_path):
        """Test that final-merger produces valid MergeOutput."""
        import sys

        sys.path.insert(0, "engines/stt-merge/final-merger")

        with patch("dalston.engine_sdk.io.upload_json"):
            from engine import FinalMergerEngine

            engine = FinalMergerEngine()

            audio_file = tmp_path / "test.wav"
            audio_file.write_bytes(b"fake audio data")

            task_input = TaskInput(
                task_id="test-task",
                job_id="job-123",
                audio_path=audio_file,
                previous_outputs={
                    "prepare": {
                        "audio_uri": "s3://bucket/audio.wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                        "engine_id": "audio-prepare",
                    },
                    "transcribe": {
                        "segments": [
                            {
                                "start": 0.0,
                                "end": 5.0,
                                "text": "Hello world",
                                "words": [
                                    {"text": "Hello", "start": 0.0, "end": 2.5},
                                    {"text": "world", "start": 2.5, "end": 5.0},
                                ],
                            }
                        ],
                        "text": "Hello world",
                        "language": "en",
                        "language_confidence": 0.98,
                        "engine_id": "faster-whisper",
                    },
                    "align": {
                        "segments": [
                            {
                                "start": 0.0,
                                "end": 5.0,
                                "text": "Hello world",
                                "words": [
                                    {
                                        "text": "Hello",
                                        "start": 0.0,
                                        "end": 2.5,
                                        "confidence": 0.95,
                                    },
                                    {
                                        "text": "world",
                                        "start": 2.5,
                                        "end": 5.0,
                                        "confidence": 0.97,
                                    },
                                ],
                            }
                        ],
                        "text": "Hello world",
                        "language": "en",
                        "word_timestamps": True,
                        "engine_id": "phoneme-align",
                    },
                },
                config={"speaker_detection": "none", "word_timestamps": True},
            )

            result = engine.process(task_input)

            # Verify output structure
            assert isinstance(result, TaskOutput)
            assert isinstance(result.data, MergeOutput)

            output = result.data
            assert output.job_id == "job-123"
            assert output.version == "1.0"
            assert output.text == "Hello world"

            # Verify metadata
            assert output.metadata.audio_duration == 10.0
            assert output.metadata.language == "en"
            assert output.metadata.word_timestamps is True
            assert output.metadata.speaker_detection == SpeakerDetectionMode.NONE

            # Verify segments
            assert len(output.segments) == 1
            segment = output.segments[0]
            assert segment.id == "seg_000"
            assert segment.text == "Hello world"
            assert segment.words is not None
            assert len(segment.words) == 2

    def test_merge_output_with_diarization(self, tmp_path):
        """Test merge output with speaker diarization."""
        import sys

        sys.path.insert(0, "engines/stt-merge/final-merger")

        with patch("dalston.engine_sdk.io.upload_json"):
            from engine import FinalMergerEngine

            engine = FinalMergerEngine()

            audio_file = tmp_path / "test.wav"
            audio_file.write_bytes(b"fake audio data")

            task_input = TaskInput(
                task_id="test-task",
                job_id="job-123",
                audio_path=audio_file,
                previous_outputs={
                    "prepare": {
                        "audio_uri": "s3://bucket/audio.wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                        "engine_id": "audio-prepare",
                    },
                    "transcribe": {
                        "segments": [
                            {"start": 0.0, "end": 5.0, "text": "Hello"},
                            {"start": 5.0, "end": 10.0, "text": "Hi there"},
                        ],
                        "text": "Hello Hi there",
                        "language": "en",
                        "engine_id": "faster-whisper",
                    },
                    "diarize": {
                        "turns": [
                            {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
                            {"speaker": "SPEAKER_01", "start": 5.0, "end": 10.0},
                        ],
                        "speakers": ["SPEAKER_00", "SPEAKER_01"],
                        "num_speakers": 2,
                        "engine_id": "pyannote-3.1",
                    },
                },
                config={"speaker_detection": "diarize"},
            )

            result = engine.process(task_input)

            output = result.data
            assert output.metadata.speaker_detection == SpeakerDetectionMode.DIARIZE
            assert output.metadata.speaker_count == 2

            # Verify speaker assignments
            assert len(output.speakers) == 2
            assert output.segments[0].speaker == "SPEAKER_00"
            assert output.segments[1].speaker == "SPEAKER_01"

    def test_merge_output_per_channel(self, tmp_path):
        """Test merge output with per-channel speaker detection."""
        import sys

        sys.path.insert(0, "engines/stt-merge/final-merger")

        with patch("dalston.engine_sdk.io.upload_json"):
            from engine import FinalMergerEngine

            engine = FinalMergerEngine()

            audio_file = tmp_path / "test.wav"
            audio_file.write_bytes(b"fake audio data")

            task_input = TaskInput(
                task_id="test-task",
                job_id="job-123",
                audio_path=audio_file,
                previous_outputs={
                    "prepare": {
                        "channel_files": [
                            {
                                "channel": 0,
                                "audio_uri": "s3://bucket/ch0.wav",
                                "duration": 10.0,
                            },
                            {
                                "channel": 1,
                                "audio_uri": "s3://bucket/ch1.wav",
                                "duration": 10.0,
                            },
                        ],
                        "split_channels": True,
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                        "original_channels": 2,
                        "engine_id": "audio-prepare",
                    },
                    "transcribe_ch0": {
                        "segments": [
                            {"start": 0.0, "end": 5.0, "text": "Agent speaking"}
                        ],
                        "text": "Agent speaking",
                        "language": "en",
                        "channel": 0,
                        "engine_id": "faster-whisper",
                    },
                    "transcribe_ch1": {
                        "segments": [
                            {"start": 2.0, "end": 7.0, "text": "Customer here"}
                        ],
                        "text": "Customer here",
                        "language": "en",
                        "channel": 1,
                        "engine_id": "faster-whisper",
                    },
                },
                config={
                    "speaker_detection": "per_channel",
                    "channel_count": 2,
                },
            )

            result = engine.process(task_input)

            output = result.data
            assert output.metadata.speaker_detection == SpeakerDetectionMode.PER_CHANNEL
            assert output.metadata.speaker_count == 2

            # Verify speakers have channel assignments
            assert len(output.speakers) == 2
            assert output.speakers[0].channel == 0
            assert output.speakers[1].channel == 1

            # Verify segments are interleaved by time
            assert len(output.segments) == 2
            # First segment should be from ch0 (starts at 0.0)
            assert output.segments[0].speaker == "SPEAKER_00"
            # Second segment should be from ch1 (starts at 2.0)
            assert output.segments[1].speaker == "SPEAKER_01"

    def test_merge_handles_skipped_alignment(self, tmp_path):
        """Test merge gracefully handles skipped alignment stage."""
        import sys

        sys.path.insert(0, "engines/stt-merge/final-merger")

        with patch("dalston.engine_sdk.io.upload_json"):
            from engine import FinalMergerEngine

            engine = FinalMergerEngine()

            audio_file = tmp_path / "test.wav"
            audio_file.write_bytes(b"fake audio data")

            task_input = TaskInput(
                task_id="test-task",
                job_id="job-123",
                audio_path=audio_file,
                previous_outputs={
                    "prepare": {
                        "audio_uri": "s3://bucket/audio.wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                        "engine_id": "audio-prepare",
                    },
                    "transcribe": {
                        "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
                        "text": "Hello",
                        "language": "xx",
                        "engine_id": "faster-whisper",
                    },
                    "align": {
                        "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
                        "text": "Hello",
                        "language": "xx",
                        "word_timestamps": False,
                        "engine_id": "phoneme-align",
                        "skipped": True,
                        "skip_reason": "No alignment model for language 'xx'",
                        "warnings": ["No alignment model for language 'xx'"],
                    },
                },
                config={"speaker_detection": "none"},
            )

            result = engine.process(task_input)

            output = result.data
            # Should still produce valid output
            assert output.text == "Hello"
            # Word timestamps should be False since alignment was skipped
            assert output.metadata.word_timestamps is False
            # Pipeline warnings should include the skip reason
            assert len(output.metadata.pipeline_warnings) > 0


class TestOutputValidation:
    """Tests for output validation against pipeline interface spec.

    These tests verify that the typed output models work correctly
    without requiring any external dependencies.
    """

    def test_transcribe_output_validates_correctly(self):
        """Test TranscribeOutput validates against the spec."""
        output = TranscribeOutput(
            segments=[
                Segment(
                    start=0.0,
                    end=5.0,
                    text="Test segment",
                    words=[
                        Word(
                            text="Test",
                            start=0.0,
                            end=2.5,
                            confidence=0.95,
                            alignment_method=AlignmentMethod.ATTENTION,
                        ),
                        Word(text="segment", start=2.5, end=5.0, confidence=0.97),
                    ],
                )
            ],
            text="Test segment",
            language="en",
            language_confidence=0.98,
            duration=5.0,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=TimestampGranularity.WORD,
            alignment_method=AlignmentMethod.ATTENTION,
            engine_id="faster-whisper",
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        # Should serialize without errors
        data = output.model_dump(mode="json")
        assert data["language"] == "en"
        assert data["timestamp_granularity_actual"] == "word"
        assert data["alignment_method"] == "attention"

    def test_align_output_validates_correctly(self):
        """Test AlignOutput validates against the spec."""
        output = AlignOutput(
            segments=[
                Segment(
                    start=0.0,
                    end=5.0,
                    text="Aligned text",
                    words=[
                        Word(
                            text="Aligned",
                            start=0.0,
                            end=2.5,
                            confidence=0.95,
                            alignment_method=AlignmentMethod.PHONEME_WAV2VEC,
                        ),
                    ],
                )
            ],
            text="Aligned text",
            language="en",
            word_timestamps=True,
            alignment_confidence=0.92,
            unaligned_words=[],
            unaligned_ratio=0.0,
            granularity_achieved=TimestampGranularity.WORD,
            engine_id="phoneme-align",
        )

        data = output.model_dump(mode="json")
        assert data["word_timestamps"] is True
        assert data["granularity_achieved"] == "word"

    def test_diarize_output_validates_correctly(self):
        """Test DiarizeOutput validates against the spec."""
        output = DiarizeOutput(
            turns=[
                SpeakerTurn(
                    speaker="SPEAKER_00",
                    start=0.0,
                    end=5.0,
                    confidence=0.95,
                ),
                SpeakerTurn(
                    speaker="SPEAKER_01",
                    start=5.0,
                    end=10.0,
                    confidence=0.92,
                    overlapping_speakers=["SPEAKER_00"],
                ),
            ],
            speakers=["SPEAKER_00", "SPEAKER_01"],
            num_speakers=2,
            overlap_duration=0.5,
            overlap_ratio=0.05,
            engine_id="pyannote-3.1",
        )

        data = output.model_dump(mode="json")
        assert data["num_speakers"] == 2
        assert len(data["turns"]) == 2
        assert data["turns"][1]["overlapping_speakers"] == ["SPEAKER_00"]

    def test_prepare_output_validates_correctly(self):
        """Test PrepareOutput validates against the spec."""
        output = PrepareOutput(
            channel_files=[
                AudioMedia(
                    uri="s3://bucket/audio.wav",
                    format="wav",
                    duration=60.5,
                    sample_rate=16000,
                    channels=1,
                    bit_depth=16,
                )
            ],
            split_channels=False,
            engine_id="audio-prepare",
        )

        data = output.model_dump(mode="json")
        assert len(data["channel_files"]) == 1
        assert data["channel_files"][0]["duration"] == 60.5
        assert data["channel_files"][0]["sample_rate"] == 16000
        assert data["channel_files"][0]["format"] == "wav"

    def test_transcribe_output_roundtrip(self):
        """Test TranscribeOutput can be serialized and deserialized."""
        original = TranscribeOutput(
            segments=[
                Segment(
                    start=0.0,
                    end=5.0,
                    text="Hello world",
                    words=[
                        Word(text="Hello", start=0.0, end=2.5, confidence=0.95),
                        Word(text="world", start=2.5, end=5.0, confidence=0.97),
                    ],
                )
            ],
            text="Hello world",
            language="en",
            language_confidence=0.98,
            timestamp_granularity_actual=TimestampGranularity.WORD,
            engine_id="faster-whisper",
        )

        # Serialize to dict (as would be stored in Redis/JSON)
        data = original.model_dump(mode="json")

        # Deserialize back
        restored = TranscribeOutput.model_validate(data)

        assert restored.text == original.text
        assert restored.language == original.language
        assert len(restored.segments) == 1
        assert len(restored.segments[0].words) == 2
        assert restored.segments[0].words[0].text == "Hello"
