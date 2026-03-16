"""Unit tests for engine SDK types (TaskRequest, TaskResponse).

Tests the typed accessors for previous stage outputs and serialization.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dalston.common.artifacts import ProducedArtifact
from dalston.common.pipeline_types import (
    AlignmentResponse,
    AudioMedia,
    DiarizationResponse,
    PreparationResponse,
    Segment,
    SpeakerTurn,
    TimestampGranularity,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
    Word,
)
from dalston.engine_sdk.types import TaskRequest, TaskResponse


class TestTaskInputBasics:
    """Tests for basic TaskRequest functionality."""

    def test_create_task_request(self):
        """Test creating a TaskRequest."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
        )
        assert task_request.task_id == "task-123"
        assert task_request.job_id == "job-456"
        assert task_request.audio_path == Path("/tmp/audio.wav")

    def test_task_request_with_config(self):
        """Test TaskRequest with configuration."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            config={"model": "large-v3", "language": "en"},
        )
        assert task_request.config["model"] == "large-v3"
        assert task_request.config["language"] == "en"

    def test_task_request_with_previous_responses(self):
        """Test TaskRequest with previous outputs dict."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "prepare": {"duration": 60.0, "sample_rate": 16000},
            },
        )
        assert "prepare" in task_request.previous_responses


class TestTaskInputGetPreparationResponse:
    """Tests for TaskRequest.get_prepare_response()."""

    def test_get_prepare_response_valid(self):
        """Test getting valid prepare output."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "prepare": {
                    "channel_files": [
                        {
                            "artifact_id": "s3://bucket/audio.wav",
                            "format": "wav",
                            "duration": 60.5,
                            "sample_rate": 16000,
                            "channels": 1,
                            "bit_depth": 16,
                        },
                    ],
                    "split_channels": False,
                    "engine_id": "audio-prepare",
                },
            },
        )
        output = task_request.get_prepare_response()
        assert output is not None
        assert isinstance(output, PreparationResponse)
        assert len(output.channel_files) == 1
        assert output.channel_files[0].duration == 60.5
        assert output.channel_files[0].sample_rate == 16000
        assert output.channel_files[0].channels == 1

    def test_get_prepare_response_missing(self):
        """Test getting prepare output when not present."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={},
        )
        output = task_request.get_prepare_response()
        assert output is None

    def test_get_prepare_response_invalid_data(self):
        """Test getting prepare output with invalid data raises validation error."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "prepare": {"invalid": "data"},  # Missing required fields
            },
        )
        with pytest.raises(ValidationError):
            task_request.get_prepare_response()

    def test_get_prepare_response_with_split_channels(self):
        """Test getting prepare output with split channel data."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "prepare": {
                    "channel_files": [
                        {
                            "artifact_id": "s3://bucket/ch0.wav",
                            "format": "wav",
                            "duration": 60.0,
                            "sample_rate": 16000,
                            "channels": 1,
                            "bit_depth": 16,
                        },
                        {
                            "artifact_id": "s3://bucket/ch1.wav",
                            "format": "wav",
                            "duration": 60.0,
                            "sample_rate": 16000,
                            "channels": 1,
                            "bit_depth": 16,
                        },
                    ],
                    "split_channels": True,
                    "engine_id": "audio-prepare",
                },
            },
        )
        output = task_request.get_prepare_response()
        assert output is not None
        assert output.split_channels is True
        assert len(output.channel_files) == 2


class TestTaskInputGetTranscript:
    """Tests for TaskRequest.get_transcript()."""

    def test_get_transcript_valid(self):
        """Test getting valid transcript."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "transcribe": {
                    "segments": [
                        {"start": 0.0, "end": 5.0, "text": "Hello world"},
                    ],
                    "text": "Hello world",
                    "language": "en",
                    "language_confidence": 0.98,
                    "engine_id": "faster-whisper",
                },
            },
        )
        output = task_request.get_transcript()
        assert output is not None
        assert isinstance(output, Transcript)
        assert output.text == "Hello world"
        assert output.language == "en"
        assert len(output.segments) == 1

    def test_get_transcript_with_words(self):
        """Test getting transcript with word timestamps."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "transcribe": {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "Hello world",
                            "words": [
                                {"text": "Hello", "start": 0.0, "end": 0.5},
                                {"text": "world", "start": 0.5, "end": 1.0},
                            ],
                        },
                    ],
                    "text": "Hello world",
                    "language": "en",
                    "timestamp_granularity": "word",
                    "engine_id": "faster-whisper",
                },
            },
        )
        output = task_request.get_transcript()
        assert output is not None
        assert output.segments[0].words is not None
        assert len(output.segments[0].words) == 2
        assert output.segments[0].words[0].text == "Hello"

    def test_get_transcript_per_channel(self):
        """Test getting transcript for specific channel."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "transcribe_ch0": {
                    "segments": [{"start": 0.0, "end": 5.0, "text": "Channel 0"}],
                    "text": "Channel 0",
                    "language": "en",
                    "engine_id": "faster-whisper",
                },
                "transcribe_ch1": {
                    "segments": [{"start": 0.0, "end": 5.0, "text": "Channel 1"}],
                    "text": "Channel 1",
                    "language": "en",
                    "engine_id": "faster-whisper",
                },
            },
        )
        ch0 = task_request.get_transcript("transcribe_ch0")
        ch1 = task_request.get_transcript("transcribe_ch1")
        assert ch0 is not None
        assert ch1 is not None
        assert ch0.text == "Channel 0"
        assert ch1.text == "Channel 1"

    def test_get_transcript_missing(self):
        """Test getting transcript when not present."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={},
        )
        output = task_request.get_transcript()
        assert output is None


class TestTaskInputGetAlignmentResponse:
    """Tests for TaskRequest.get_align_response()."""

    def test_get_align_response_valid(self):
        """Test getting valid align output."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "align": {
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "Hello",
                            "words": [
                                {
                                    "text": "Hello",
                                    "start": 0.0,
                                    "end": 1.0,
                                    "confidence": 0.95,
                                },
                            ],
                        },
                    ],
                    "text": "Hello",
                    "language": "en",
                    "word_timestamps": True,
                    "alignment_confidence": 0.92,
                    "unaligned_words": [],
                    "unaligned_ratio": 0.0,
                    "granularity_achieved": "word",
                    "engine_id": "phoneme-align",
                },
            },
        )
        output = task_request.get_align_response()
        assert output is not None
        assert isinstance(output, AlignmentResponse)
        assert output.word_timestamps is True
        assert output.alignment_confidence == 0.92

    def test_get_align_response_skipped(self):
        """Test getting align output when skipped."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "align": {
                    "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}],
                    "text": "Hello",
                    "language": "xx",
                    "word_timestamps": False,
                    "engine_id": "phoneme-align",
                    "skipped": True,
                    "skip_reason": "No alignment model for language 'xx'",
                    "warnings": ["No alignment model for language 'xx'"],
                },
            },
        )
        output = task_request.get_align_response()
        assert output is not None
        assert output.skipped is True
        assert output.skip_reason is not None

    def test_get_align_response_per_channel(self):
        """Test getting align output for specific channel."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "align_ch0": {
                    "segments": [{"start": 0.0, "end": 1.0, "text": "Ch0"}],
                    "text": "Ch0",
                    "language": "en",
                    "word_timestamps": True,
                    "engine_id": "phoneme-align",
                },
            },
        )
        output = task_request.get_align_response("align_ch0")
        assert output is not None
        assert output.text == "Ch0"


class TestTaskInputGetDiarizationResponse:
    """Tests for TaskRequest.get_diarize_response()."""

    def test_get_diarize_response_valid(self):
        """Test getting valid diarize output."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "diarize": {
                    "turns": [
                        {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
                        {"speaker": "SPEAKER_01", "start": 5.0, "end": 10.0},
                    ],
                    "speakers": ["SPEAKER_00", "SPEAKER_01"],
                    "num_speakers": 2,
                    "overlap_duration": 0.0,
                    "overlap_ratio": 0.0,
                    "engine_id": "pyannote-4.0",
                },
            },
        )
        output = task_request.get_diarize_response()
        assert output is not None
        assert isinstance(output, DiarizationResponse)
        assert output.num_speakers == 2
        assert len(output.turns) == 2
        assert output.turns[0].speaker == "SPEAKER_00"

    def test_get_diarize_response_with_overlap(self):
        """Test getting diarize output with overlapping speech."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "diarize": {
                    "turns": [
                        {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
                        {
                            "speaker": "SPEAKER_01",
                            "start": 4.5,
                            "end": 10.0,
                            "overlapping_speakers": ["SPEAKER_00"],
                        },
                    ],
                    "speakers": ["SPEAKER_00", "SPEAKER_01"],
                    "num_speakers": 2,
                    "overlap_duration": 0.5,
                    "overlap_ratio": 0.05,
                    "engine_id": "pyannote-4.0",
                },
            },
        )
        output = task_request.get_diarize_response()
        assert output is not None
        assert output.overlap_duration == 0.5
        assert output.turns[1].overlapping_speakers == ["SPEAKER_00"]

    def test_get_diarize_response_skipped(self):
        """Test getting diarize output when skipped."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "diarize": {
                    "turns": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 999999.0}],
                    "speakers": ["SPEAKER_00"],
                    "num_speakers": 1,
                    "engine_id": "pyannote-4.0",
                    "skipped": True,
                    "skip_reason": "DIARIZATION_DISABLED=true",
                },
            },
        )
        output = task_request.get_diarize_response()
        assert output is not None
        assert output.skipped is True


class TestTaskInputGetRawOutput:
    """Tests for TaskRequest.get_raw_response()."""

    def test_get_raw_response(self):
        """Test getting raw output dict."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "transcribe": {
                    "text": "Hello",
                    "language": "en",
                    "custom_field": "value",
                },
            },
        )
        raw = task_request.get_raw_response("transcribe")
        assert raw is not None
        assert raw["text"] == "Hello"
        assert raw["custom_field"] == "value"

    def test_get_raw_response_missing(self):
        """Test getting raw output when not present."""
        task_request = TaskRequest(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={},
        )
        raw = task_request.get_raw_response("transcribe")
        assert raw is None


class TestTaskOutput:
    """Tests for TaskResponse class."""

    def test_create_task_output_with_dict(self):
        """Test creating TaskResponse with dict data."""
        output = TaskResponse(
            data={
                "text": "Hello world",
                "language": "en",
            }
        )
        assert output.data["text"] == "Hello world"

    def test_create_task_output_with_pydantic_model(self):
        """Test creating TaskResponse with Pydantic model."""
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="Hello")],
            text="Hello",
            language="en",
            engine_id="test",
        )
        output = TaskResponse(data=transcript)
        assert isinstance(output.data, Transcript)

    def test_task_output_to_dict_from_pydantic(self):
        """Test TaskResponse.to_dict() with Pydantic model."""
        transcript = Transcript(
            segments=[
                TranscriptSegment(
                    start=0.0,
                    end=1.0,
                    text="Hello",
                    words=[
                        TranscriptWord(
                            text="Hello", start=0.0, end=1.0, confidence=0.95
                        )
                    ],
                )
            ],
            text="Hello",
            language="en",
            timestamp_granularity=TimestampGranularity.WORD,
            engine_id="test",
        )
        output = TaskResponse(data=transcript)
        data = output.to_dict()

        assert isinstance(data, dict)
        assert data["text"] == "Hello"
        assert data["language"] == "en"
        assert data["timestamp_granularity"] == "word"
        assert len(data["segments"]) == 1
        assert data["segments"][0]["words"][0]["text"] == "Hello"

    def test_task_output_to_dict_from_dict(self):
        """Test TaskResponse.to_dict() with dict data."""
        output = TaskResponse(
            data={
                "text": "Hello",
                "custom": "value",
            }
        )
        data = output.to_dict()
        assert data["text"] == "Hello"
        assert data["custom"] == "value"

    def test_task_output_with_produced_artifacts(self):
        """Test TaskResponse with produced artifact descriptors."""
        output = TaskResponse(
            data={"result": "success"},
            produced_artifacts=[
                ProducedArtifact(
                    logical_name="waveform",
                    local_path=Path("/tmp/waveform.png"),
                    kind="image",
                ),
                ProducedArtifact(
                    logical_name="spectrogram",
                    local_path=Path("/tmp/spectrogram.png"),
                    kind="image",
                ),
            ],
        )
        assert output.produced_artifacts
        assert output.produced_artifacts[0].logical_name == "waveform"


class TestTaskInputTypedOutputIntegration:
    """Integration tests for typed output flow between stages."""

    def test_transcribe_to_align_flow(self):
        """Test transcribe output can be consumed by align stage."""
        # Simulate transcript
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=5.0, text="Hello world")],
            text="Hello world",
            language="en",
            language_confidence=0.98,
            engine_id="faster-whisper",
        )

        # Create task input for align stage with serialized transcript
        task_request = TaskRequest(
            task_id="align-task",
            job_id="job-123",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "transcribe": transcript.model_dump(mode="json"),
            },
        )

        # Align stage gets typed output
        output = task_request.get_transcript()
        assert output is not None
        assert output.text == "Hello world"
        assert output.language == "en"
        assert len(output.segments) == 1

    def test_all_stages_to_merge_flow(self):
        """Test all stage outputs can be consumed by merge stage."""
        # Simulate all upstream outputs
        prepare_response = PreparationResponse(
            channel_files=[
                AudioMedia(
                    artifact_id="s3://bucket/audio.wav",
                    format="wav",
                    duration=60.0,
                    sample_rate=16000,
                    channels=1,
                    bit_depth=16,
                ),
            ],
            split_channels=False,
            engine_id="audio-prepare",
        )

        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=5.0, text="Hello")],
            text="Hello",
            language="en",
            engine_id="faster-whisper",
        )

        align_response = AlignmentResponse(
            segments=[
                Segment(
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    words=[Word(text="Hello", start=0.0, end=5.0)],
                )
            ],
            text="Hello",
            language="en",
            word_timestamps=True,
            engine_id="phoneme-align",
        )

        diarize_response = DiarizationResponse(
            turns=[SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.0)],
            speakers=["SPEAKER_00"],
            num_speakers=1,
            engine_id="pyannote-4.0",
        )

        # Create merge task request
        task_request = TaskRequest(
            task_id="merge-task",
            job_id="job-123",
            audio_path=Path("/tmp/audio.wav"),
            previous_responses={
                "prepare": prepare_response.model_dump(mode="json"),
                "transcribe": transcript.model_dump(mode="json"),
                "align": align_response.model_dump(mode="json"),
                "diarize": diarize_response.model_dump(mode="json"),
            },
        )

        # Merge stage gets all typed outputs
        prepare = task_request.get_prepare_response()
        transcribe = task_request.get_transcript()
        align = task_request.get_align_response()
        diarize = task_request.get_diarize_response()

        assert prepare is not None
        assert len(prepare.channel_files) == 1
        assert prepare.channel_files[0].duration == 60.0

        assert transcribe is not None
        assert transcribe.text == "Hello"

        assert align is not None
        assert align.word_timestamps is True
        assert align.segments[0].words is not None

        assert diarize is not None
        assert diarize.num_speakers == 1
        assert diarize.turns[0].speaker == "SPEAKER_00"
