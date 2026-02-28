"""Unit tests for engine SDK types (TaskInput, TaskOutput).

Tests the typed accessors for previous stage outputs and serialization.
"""

from pathlib import Path

from dalston.common.pipeline_types import (
    AlignOutput,
    AudioMedia,
    DiarizeOutput,
    PrepareOutput,
    Segment,
    SpeakerTurn,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)
from dalston.engine_sdk.types import TaskInput, TaskOutput


class TestTaskInputBasics:
    """Tests for basic TaskInput functionality."""

    def test_create_task_input(self):
        """Test creating a TaskInput."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
        )
        assert task_input.task_id == "task-123"
        assert task_input.job_id == "job-456"
        assert task_input.audio_path == Path("/tmp/audio.wav")

    def test_task_input_with_config(self):
        """Test TaskInput with configuration."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            config={"model": "large-v3", "language": "en"},
        )
        assert task_input.config["model"] == "large-v3"
        assert task_input.config["language"] == "en"

    def test_task_input_with_previous_outputs(self):
        """Test TaskInput with previous outputs dict."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "prepare": {"duration": 60.0, "sample_rate": 16000},
            },
        )
        assert "prepare" in task_input.previous_outputs


class TestTaskInputGetPrepareOutput:
    """Tests for TaskInput.get_prepare_output()."""

    def test_get_prepare_output_valid(self):
        """Test getting valid prepare output."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "prepare": {
                    "channel_files": [
                        {
                            "uri": "s3://bucket/audio.wav",
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
        output = task_input.get_prepare_output()
        assert output is not None
        assert isinstance(output, PrepareOutput)
        assert len(output.channel_files) == 1
        assert output.channel_files[0].duration == 60.5
        assert output.channel_files[0].sample_rate == 16000
        assert output.channel_files[0].channels == 1

    def test_get_prepare_output_missing(self):
        """Test getting prepare output when not present."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={},
        )
        output = task_input.get_prepare_output()
        assert output is None

    def test_get_prepare_output_invalid_data(self):
        """Test getting prepare output with invalid data returns None."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "prepare": {"invalid": "data"},  # Missing required fields
            },
        )
        output = task_input.get_prepare_output()
        assert output is None

    def test_get_prepare_output_with_split_channels(self):
        """Test getting prepare output with split channel data."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "prepare": {
                    "channel_files": [
                        {
                            "uri": "s3://bucket/ch0.wav",
                            "format": "wav",
                            "duration": 60.0,
                            "sample_rate": 16000,
                            "channels": 1,
                            "bit_depth": 16,
                        },
                        {
                            "uri": "s3://bucket/ch1.wav",
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
        output = task_input.get_prepare_output()
        assert output is not None
        assert output.split_channels is True
        assert len(output.channel_files) == 2


class TestTaskInputGetTranscribeOutput:
    """Tests for TaskInput.get_transcribe_output()."""

    def test_get_transcribe_output_valid(self):
        """Test getting valid transcribe output."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        output = task_input.get_transcribe_output()
        assert output is not None
        assert isinstance(output, TranscribeOutput)
        assert output.text == "Hello world"
        assert output.language == "en"
        assert len(output.segments) == 1

    def test_get_transcribe_output_with_words(self):
        """Test getting transcribe output with word timestamps."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
                    "timestamp_granularity_actual": "word",
                    "engine_id": "faster-whisper",
                },
            },
        )
        output = task_input.get_transcribe_output()
        assert output is not None
        assert output.segments[0].words is not None
        assert len(output.segments[0].words) == 2
        assert output.segments[0].words[0].text == "Hello"

    def test_get_transcribe_output_per_channel(self):
        """Test getting transcribe output for specific channel."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        ch0 = task_input.get_transcribe_output("transcribe_ch0")
        ch1 = task_input.get_transcribe_output("transcribe_ch1")
        assert ch0 is not None
        assert ch1 is not None
        assert ch0.text == "Channel 0"
        assert ch1.text == "Channel 1"

    def test_get_transcribe_output_missing(self):
        """Test getting transcribe output when not present."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={},
        )
        output = task_input.get_transcribe_output()
        assert output is None


class TestTaskInputGetAlignOutput:
    """Tests for TaskInput.get_align_output()."""

    def test_get_align_output_valid(self):
        """Test getting valid align output."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        output = task_input.get_align_output()
        assert output is not None
        assert isinstance(output, AlignOutput)
        assert output.word_timestamps is True
        assert output.alignment_confidence == 0.92

    def test_get_align_output_skipped(self):
        """Test getting align output when skipped."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        output = task_input.get_align_output()
        assert output is not None
        assert output.skipped is True
        assert output.skip_reason is not None

    def test_get_align_output_per_channel(self):
        """Test getting align output for specific channel."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "align_ch0": {
                    "segments": [{"start": 0.0, "end": 1.0, "text": "Ch0"}],
                    "text": "Ch0",
                    "language": "en",
                    "word_timestamps": True,
                    "engine_id": "phoneme-align",
                },
            },
        )
        output = task_input.get_align_output("align_ch0")
        assert output is not None
        assert output.text == "Ch0"


class TestTaskInputGetDiarizeOutput:
    """Tests for TaskInput.get_diarize_output()."""

    def test_get_diarize_output_valid(self):
        """Test getting valid diarize output."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        output = task_input.get_diarize_output()
        assert output is not None
        assert isinstance(output, DiarizeOutput)
        assert output.num_speakers == 2
        assert len(output.turns) == 2
        assert output.turns[0].speaker == "SPEAKER_00"

    def test_get_diarize_output_with_overlap(self):
        """Test getting diarize output with overlapping speech."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        output = task_input.get_diarize_output()
        assert output is not None
        assert output.overlap_duration == 0.5
        assert output.turns[1].overlapping_speakers == ["SPEAKER_00"]

    def test_get_diarize_output_skipped(self):
        """Test getting diarize output when skipped."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
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
        output = task_input.get_diarize_output()
        assert output is not None
        assert output.skipped is True


class TestTaskInputGetRawOutput:
    """Tests for TaskInput.get_raw_output()."""

    def test_get_raw_output(self):
        """Test getting raw output dict."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "transcribe": {
                    "text": "Hello",
                    "language": "en",
                    "custom_field": "value",
                },
            },
        )
        raw = task_input.get_raw_output("transcribe")
        assert raw is not None
        assert raw["text"] == "Hello"
        assert raw["custom_field"] == "value"

    def test_get_raw_output_missing(self):
        """Test getting raw output when not present."""
        task_input = TaskInput(
            task_id="task-123",
            job_id="job-456",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={},
        )
        raw = task_input.get_raw_output("transcribe")
        assert raw is None


class TestTaskOutput:
    """Tests for TaskOutput class."""

    def test_create_task_output_with_dict(self):
        """Test creating TaskOutput with dict data."""
        output = TaskOutput(
            data={
                "text": "Hello world",
                "language": "en",
            }
        )
        assert output.data["text"] == "Hello world"

    def test_create_task_output_with_pydantic_model(self):
        """Test creating TaskOutput with Pydantic model."""
        transcribe_output = TranscribeOutput(
            segments=[Segment(start=0.0, end=1.0, text="Hello")],
            text="Hello",
            language="en",
            engine_id="test",
        )
        output = TaskOutput(data=transcribe_output)
        assert isinstance(output.data, TranscribeOutput)

    def test_task_output_to_dict_from_pydantic(self):
        """Test TaskOutput.to_dict() with Pydantic model."""
        transcribe_output = TranscribeOutput(
            segments=[
                Segment(
                    start=0.0,
                    end=1.0,
                    text="Hello",
                    words=[Word(text="Hello", start=0.0, end=1.0, confidence=0.95)],
                )
            ],
            text="Hello",
            language="en",
            timestamp_granularity_actual=TimestampGranularity.WORD,
            engine_id="test",
        )
        output = TaskOutput(data=transcribe_output)
        data = output.to_dict()

        assert isinstance(data, dict)
        assert data["text"] == "Hello"
        assert data["language"] == "en"
        assert data["timestamp_granularity_actual"] == "word"
        assert len(data["segments"]) == 1
        assert data["segments"][0]["words"][0]["text"] == "Hello"

    def test_task_output_to_dict_from_dict(self):
        """Test TaskOutput.to_dict() with dict data."""
        output = TaskOutput(
            data={
                "text": "Hello",
                "custom": "value",
            }
        )
        data = output.to_dict()
        assert data["text"] == "Hello"
        assert data["custom"] == "value"

    def test_task_output_with_artifacts(self):
        """Test TaskOutput with artifacts."""
        output = TaskOutput(
            data={"result": "success"},
            artifacts={
                "waveform": Path("/tmp/waveform.png"),
                "spectrogram": Path("/tmp/spectrogram.png"),
            },
        )
        assert output.artifacts is not None
        assert "waveform" in output.artifacts


class TestTaskInputTypedOutputIntegration:
    """Integration tests for typed output flow between stages."""

    def test_transcribe_to_align_flow(self):
        """Test transcribe output can be consumed by align stage."""
        # Simulate transcribe output
        transcribe_output = TranscribeOutput(
            segments=[Segment(start=0.0, end=5.0, text="Hello world")],
            text="Hello world",
            language="en",
            language_confidence=0.98,
            engine_id="faster-whisper",
        )

        # Create task input for align stage with serialized transcribe output
        task_input = TaskInput(
            task_id="align-task",
            job_id="job-123",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "transcribe": transcribe_output.model_dump(mode="json"),
            },
        )

        # Align stage gets typed output
        output = task_input.get_transcribe_output()
        assert output is not None
        assert output.text == "Hello world"
        assert output.language == "en"
        assert len(output.segments) == 1

    def test_all_stages_to_merge_flow(self):
        """Test all stage outputs can be consumed by merge stage."""
        # Simulate all upstream outputs
        prepare_output = PrepareOutput(
            channel_files=[
                AudioMedia(
                    uri="s3://bucket/audio.wav",
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

        transcribe_output = TranscribeOutput(
            segments=[Segment(start=0.0, end=5.0, text="Hello")],
            text="Hello",
            language="en",
            engine_id="faster-whisper",
        )

        align_output = AlignOutput(
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

        diarize_output = DiarizeOutput(
            turns=[SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.0)],
            speakers=["SPEAKER_00"],
            num_speakers=1,
            engine_id="pyannote-4.0",
        )

        # Create merge task input
        task_input = TaskInput(
            task_id="merge-task",
            job_id="job-123",
            audio_path=Path("/tmp/audio.wav"),
            previous_outputs={
                "prepare": prepare_output.model_dump(mode="json"),
                "transcribe": transcribe_output.model_dump(mode="json"),
                "align": align_output.model_dump(mode="json"),
                "diarize": diarize_output.model_dump(mode="json"),
            },
        )

        # Merge stage gets all typed outputs
        prepare = task_input.get_prepare_output()
        transcribe = task_input.get_transcribe_output()
        align = task_input.get_align_output()
        diarize = task_input.get_diarize_output()

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
