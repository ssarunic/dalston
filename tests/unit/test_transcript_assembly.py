"""Unit tests for M68 transcript assembly module.

Tests the shared transcript assembly logic that replaces the merge engine
for linear pipelines.
"""

from dalston.common.pipeline_types import (
    AlignOutput,
    Segment,
    SpeakerTurn,
    Transcript,
    TranscriptSegment,
    Word,
)
from dalston.common.transcript import (
    _build_merged_segments,
    _extract_audio_metadata,
    _find_speaker_by_overlap,
    _select_segments,
    assemble_per_channel_transcript,
    assemble_transcript,
    determine_terminal_stage,
)

# ---------------------------------------------------------------------------
# assemble_transcript – basic cases
# ---------------------------------------------------------------------------


class TestAssembleTranscriptBasic:
    """Tests for the top-level assemble_transcript function."""

    def test_minimal_transcribe_only(self):
        """Assemble transcript from transcribe output alone."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "runtime": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello world",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello world"},
                ],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_transcript(
            job_id="job-1",
            stage_outputs=stage_outputs,
        )

        assert result.job_id == "job-1"
        assert result.text == "Hello world"
        assert len(result.segments) == 1
        assert result.segments[0].id == "seg_000"
        assert result.segments[0].text == "Hello world"
        assert result.segments[0].speaker is None
        assert result.metadata.language == "en"
        assert result.metadata.audio_duration == 10.0

    def test_with_alignment(self):
        """Assemble transcript preferring align output segments."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 5.0,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "runtime": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello world",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello world"},
                ],
                "runtime": "faster-whisper",
            },
            "align": {
                "segments": [
                    {
                        "start": 0.1,
                        "end": 1.9,
                        "text": "Hello world",
                        "words": [
                            {"text": "Hello", "start": 0.1, "end": 0.5},
                            {"text": "world", "start": 0.6, "end": 1.9},
                        ],
                    },
                ],
                "text": "Hello world",
                "language": "en",
                "word_timestamps": True,
                "unaligned_ratio": 0.0,
                "granularity_achieved": "word",
                "runtime": "phoneme-align",
            },
        }

        result = assemble_transcript(
            job_id="job-2",
            stage_outputs=stage_outputs,
            word_timestamps_requested=True,
        )

        assert len(result.segments) == 1
        # Aligned segments should have adjusted timestamps
        assert result.segments[0].start == 0.1
        assert result.segments[0].end == 1.9
        assert result.metadata.word_timestamps is True

    def test_with_diarization(self):
        """Assemble transcript with speaker assignments from diarize output."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "runtime": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello. How are you?",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello."},
                    {"start": 3.0, "end": 5.0, "text": "How are you?"},
                ],
                "runtime": "faster-whisper",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.5},
                    {"speaker": "SPEAKER_01", "start": 2.5, "end": 5.5},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "runtime": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="job-3",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        assert len(result.segments) == 2
        assert result.segments[0].speaker == "SPEAKER_00"
        assert result.segments[1].speaker == "SPEAKER_01"
        assert len(result.speakers) == 2
        assert result.speakers[0].id == "SPEAKER_00"
        assert result.speakers[1].id == "SPEAKER_01"
        assert result.metadata.speaker_count == 2

    def test_known_speaker_names(self):
        """Assemble transcript applying known speaker names."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "runtime": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}],
                "runtime": "faster-whisper",
            },
            "diarize": {
                "turns": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.5}],
                "speakers": ["SPEAKER_00"],
                "num_speakers": 1,
                "runtime": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="job-4",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
            known_speaker_names=["Alice"],
        )

        assert result.speakers[0].label == "Alice"

    def test_empty_prepare_output(self):
        """Assemble transcript with missing prepare output uses defaults."""
        stage_outputs = {
            "transcribe": {
                "text": "Test",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Test"}],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_transcript(
            job_id="job-5",
            stage_outputs=stage_outputs,
        )

        assert result.metadata.audio_duration == 0.0
        assert result.metadata.sample_rate == 16000

    def test_pipeline_stages_inferred(self):
        """Pipeline stages are inferred from available outputs."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 5.0,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "runtime": "audio-prepare",
            },
            "transcribe": {
                "text": "Test",
                "language": "en",
                "segments": [],
                "runtime": "faster-whisper",
            },
            "align": {
                "segments": [],
                "text": "Test",
                "language": "en",
                "word_timestamps": False,
                "unaligned_ratio": 0.0,
                "granularity_achieved": "word",
                "runtime": "phoneme-align",
            },
        }

        result = assemble_transcript(
            job_id="job-6",
            stage_outputs=stage_outputs,
        )

        assert "prepare" in result.metadata.pipeline_stages
        assert "transcribe" in result.metadata.pipeline_stages
        assert "align" in result.metadata.pipeline_stages

    def test_explicit_pipeline_stages(self):
        """Pipeline stages can be explicitly provided."""
        stage_outputs = {
            "transcribe": {
                "text": "Test",
                "language": "en",
                "segments": [],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_transcript(
            job_id="job-7",
            stage_outputs=stage_outputs,
            pipeline_stages=["prepare", "transcribe"],
        )

        assert result.metadata.pipeline_stages == ["prepare", "transcribe"]

    def test_no_pii_in_linear_pipeline(self):
        """Linear pipeline transcript has no PII fields set."""
        stage_outputs = {
            "transcribe": {
                "text": "Test",
                "language": "en",
                "segments": [],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_transcript(
            job_id="job-8",
            stage_outputs=stage_outputs,
        )

        assert result.redacted_text is None
        assert result.pii_entities is None
        assert result.pii_metadata is None


# ---------------------------------------------------------------------------
# _find_speaker_by_overlap
# ---------------------------------------------------------------------------


class TestFindSpeakerByOverlap:
    """Tests for speaker overlap matching."""

    def test_single_speaker_full_overlap(self):
        turns = [SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=10.0)]
        assert _find_speaker_by_overlap(1.0, 3.0, turns) == "SPEAKER_00"

    def test_picks_maximum_overlap(self):
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=2.0),
            SpeakerTurn(speaker="SPEAKER_01", start=1.5, end=5.0),
        ]
        # Segment 1.0-4.0: overlap with SPEAKER_00 = 1.0s, SPEAKER_01 = 2.5s
        assert _find_speaker_by_overlap(1.0, 4.0, turns) == "SPEAKER_01"

    def test_no_overlap(self):
        turns = [SpeakerTurn(speaker="SPEAKER_00", start=5.0, end=10.0)]
        assert _find_speaker_by_overlap(0.0, 2.0, turns) is None

    def test_empty_turns(self):
        assert _find_speaker_by_overlap(0.0, 1.0, []) is None


# ---------------------------------------------------------------------------
# _extract_audio_metadata
# ---------------------------------------------------------------------------


class TestExtractAudioMetadata:
    """Tests for audio metadata extraction from prepare output."""

    def test_from_channel_files(self):
        data = {
            "channel_files": [
                {
                    "artifact_id": "a1",
                    "format": "wav",
                    "duration": 120.5,
                    "sample_rate": 44100,
                    "channels": 2,
                }
            ]
        }
        duration, channels, rate = _extract_audio_metadata(data)
        assert duration == 120.5
        assert channels == 2
        assert rate == 44100

    def test_empty_data_defaults(self):
        duration, channels, rate = _extract_audio_metadata({})
        assert duration == 0.0
        assert channels == 1
        assert rate == 16000

    def test_none_data_defaults(self):
        duration, channels, rate = _extract_audio_metadata(None)
        assert duration == 0.0
        assert channels == 1
        assert rate == 16000

    def test_flat_keys_fallback(self):
        data = {"duration": 30.0, "channels": 1, "sample_rate": 22050}
        duration, channels, rate = _extract_audio_metadata(data)
        assert duration == 30.0
        assert channels == 1
        assert rate == 22050


# ---------------------------------------------------------------------------
# _select_segments
# ---------------------------------------------------------------------------


class TestSelectSegments:
    """Tests for segment source selection."""

    def test_prefers_align_over_transcribe(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="orig")],
            text="orig",
            language="en",
            runtime="faster-whisper",
        )
        align = AlignOutput(
            segments=[Segment(start=0.1, end=0.9, text="aligned")],
            text="aligned",
            language="en",
            word_timestamps=True,
            unaligned_ratio=0.0,
            granularity_achieved="word",
            runtime="phoneme-align",
        )

        segments, has_words, warnings = _select_segments(
            transcript_v1=transcript,
            align_output=align,
            raw_segments=[],
        )

        assert len(segments) == 1
        assert segments[0].text == "aligned"
        assert has_words is True

    def test_falls_back_to_transcribe_when_align_skipped(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="orig")],
            text="orig",
            language="en",
            runtime="faster-whisper",
        )
        align = AlignOutput(
            segments=[],
            text="",
            language="en",
            word_timestamps=False,
            unaligned_ratio=0.0,
            granularity_achieved="word",
            runtime="phoneme-align",
            skipped=True,
            skip_reason="unsupported language",
        )

        segments, has_words, warnings = _select_segments(
            transcript_v1=transcript,
            align_output=align,
            raw_segments=[],
        )

        assert len(segments) == 1
        assert segments[0].text == "orig"
        assert has_words is False

    def test_uses_raw_segments_as_last_resort(self):
        raw = [{"start": 0.0, "end": 1.0, "text": "raw"}]

        segments, has_words, warnings = _select_segments(
            transcript_v1=None,
            align_output=None,
            raw_segments=raw,
        )

        assert len(segments) == 1
        assert has_words is False


# ---------------------------------------------------------------------------
# _build_merged_segments
# ---------------------------------------------------------------------------


class TestBuildMergedSegments:
    """Tests for merged segment building with IDs and speakers."""

    def test_assigns_sequential_ids(self):
        source = [
            Segment(start=0.0, end=1.0, text="First"),
            Segment(start=1.0, end=2.0, text="Second"),
            Segment(start=2.0, end=3.0, text="Third"),
        ]

        result = _build_merged_segments(
            segments_source=source,
            diarization_turns=[],
            word_timestamps_available=False,
        )

        assert [s.id for s in result] == ["seg_000", "seg_001", "seg_002"]

    def test_assigns_speakers_from_turns(self):
        source = [
            Segment(start=0.0, end=2.0, text="Hello"),
            Segment(start=3.0, end=5.0, text="Hi there"),
        ]
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=2.5),
            SpeakerTurn(speaker="SPEAKER_01", start=2.5, end=6.0),
        ]

        result = _build_merged_segments(
            segments_source=source,
            diarization_turns=turns,
            word_timestamps_available=False,
        )

        assert result[0].speaker == "SPEAKER_00"
        assert result[1].speaker == "SPEAKER_01"

    def test_handles_raw_dict_segments(self):
        source = [
            {"start": 0.0, "end": 1.0, "text": "Hello", "tokens": [1, 2, 3]},
        ]

        result = _build_merged_segments(
            segments_source=source,
            diarization_turns=[],
            word_timestamps_available=False,
        )

        assert len(result) == 1
        assert result[0].text == "Hello"
        assert result[0].tokens == [1, 2, 3]

    def test_normalizes_words(self):
        source = [
            Segment(
                start=0.0,
                end=2.0,
                text="Hello world",
                words=[
                    Word(text="Hello", start=0.0, end=0.5),
                    Word(text="world", start=0.6, end=2.0),
                ],
            ),
        ]

        result = _build_merged_segments(
            segments_source=source,
            diarization_turns=[],
            word_timestamps_available=True,
        )

        assert result[0].words is not None
        assert len(result[0].words) == 2
        assert result[0].words[0].text == "Hello"


# ---------------------------------------------------------------------------
# determine_terminal_stage
# ---------------------------------------------------------------------------


class TestDetermineTerminalStage:
    """Tests for terminal stage determination."""

    def test_transcribe_only(self):
        assert determine_terminal_stage() == "transcribe"

    def test_with_align(self):
        assert determine_terminal_stage(has_align=True) == "align"

    def test_with_diarize(self):
        assert (
            determine_terminal_stage(has_diarize=True, speaker_detection="diarize")
            == "diarize"
        )

    def test_with_align_and_diarize(self):
        """Diarize is terminal when both align and diarize are present."""
        assert (
            determine_terminal_stage(
                has_align=True, has_diarize=True, speaker_detection="diarize"
            )
            == "diarize"
        )

    def test_diarize_without_speaker_detection_mode(self):
        """Diarize is not terminal if speaker_detection is not 'diarize'."""
        assert (
            determine_terminal_stage(has_diarize=True, speaker_detection="none")
            == "transcribe"
        )


# ---------------------------------------------------------------------------
# assemble_per_channel_transcript
# ---------------------------------------------------------------------------


class TestAssemblePerChannelTranscript:
    """Tests for per-channel transcript assembly."""

    def test_basic_two_channel(self):
        """Assemble transcript from two channel transcriptions."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 10.0,
                        "sample_rate": 16000,
                        "channels": 2,
                    }
                ],
            },
            "transcribe_ch0": {
                "text": "Hello from channel zero.",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello from channel zero."},
                ],
                "runtime": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hi from channel one.",
                "language": "en",
                "segments": [
                    {"start": 1.0, "end": 3.0, "text": "Hi from channel one."},
                ],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-1",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        assert result.job_id == "job-pc-1"
        assert len(result.segments) == 2
        # Segments are sorted by start time
        assert result.segments[0].start == 0.0
        assert result.segments[0].speaker == "SPEAKER_00"
        assert result.segments[1].start == 1.0
        assert result.segments[1].speaker == "SPEAKER_01"
        # Speakers array has channel attribute
        assert len(result.speakers) == 2
        assert result.speakers[0].id == "SPEAKER_00"
        assert result.speakers[0].channel == 0
        assert result.speakers[1].id == "SPEAKER_01"
        assert result.speakers[1].channel == 1
        # Metadata
        assert result.metadata.speaker_detection.value == "per_channel"
        assert result.metadata.speaker_count == 2
        assert result.metadata.audio_duration == 10.0

    def test_interleaved_segments_sorted_by_time(self):
        """Segments from multiple channels are interleaved by start time."""
        stage_outputs = {
            "prepare": {"duration": 10.0, "channels": 2, "sample_rate": 16000},
            "transcribe_ch0": {
                "text": "A. C.",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "A."},
                    {"start": 4.0, "end": 5.0, "text": "C."},
                ],
                "runtime": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "B.",
                "language": "en",
                "segments": [
                    {"start": 2.0, "end": 3.0, "text": "B."},
                ],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-2",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        assert len(result.segments) == 3
        assert [s.text for s in result.segments] == ["A.", "B.", "C."]
        assert [s.speaker for s in result.segments] == [
            "SPEAKER_00",
            "SPEAKER_01",
            "SPEAKER_00",
        ]
        assert [s.id for s in result.segments] == ["seg_000", "seg_001", "seg_002"]

    def test_with_alignment(self):
        """Per-channel assembly uses align output when available."""
        stage_outputs = {
            "prepare": {"duration": 5.0, "channels": 2, "sample_rate": 16000},
            "transcribe_ch0": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 0.0, "end": 2.0, "text": "Hello"}],
                "runtime": "faster-whisper",
            },
            "align_ch0": {
                "segments": [
                    {
                        "start": 0.1,
                        "end": 1.8,
                        "text": "Hello",
                        "words": [{"text": "Hello", "start": 0.1, "end": 1.8}],
                    },
                ],
                "text": "Hello",
                "language": "en",
                "word_timestamps": True,
                "unaligned_ratio": 0.0,
                "granularity_achieved": "word",
                "runtime": "phoneme-align",
            },
            "transcribe_ch1": {
                "text": "World",
                "language": "en",
                "segments": [{"start": 1.0, "end": 3.0, "text": "World"}],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-3",
            stage_outputs=stage_outputs,
            channel_count=2,
            word_timestamps_requested=True,
        )

        assert len(result.segments) == 2
        # Channel 0 uses aligned timestamps
        assert result.segments[0].start == 0.1
        assert result.segments[0].end == 1.8
        assert result.metadata.word_timestamps is True

    def test_known_speaker_names(self):
        """Per-channel assembly applies known speaker names."""
        stage_outputs = {
            "prepare": {"duration": 5.0, "channels": 2, "sample_rate": 16000},
            "transcribe_ch0": {
                "text": "Hi",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hi"}],
                "runtime": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 1.0, "end": 2.0, "text": "Hello"}],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-4",
            stage_outputs=stage_outputs,
            channel_count=2,
            known_speaker_names=["Alice", "Bob"],
        )

        assert result.speakers[0].label == "Alice"
        assert result.speakers[1].label == "Bob"

    def test_full_text_from_interleaved(self):
        """Full text is built from interleaved segments."""
        stage_outputs = {
            "prepare": {"duration": 5.0, "channels": 2, "sample_rate": 16000},
            "transcribe_ch0": {
                "text": "Hello.",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hello."}],
                "runtime": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hi there.",
                "language": "en",
                "segments": [{"start": 2.0, "end": 3.0, "text": "Hi there."}],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-5",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        assert result.text == "Hello. Hi there."

    def test_no_pii_fields(self):
        """Per-channel assembly has no PII fields set."""
        stage_outputs = {
            "transcribe_ch0": {
                "text": "Test",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Test"}],
                "runtime": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Data",
                "language": "en",
                "segments": [{"start": 1.0, "end": 2.0, "text": "Data"}],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-6",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        assert result.redacted_text is None
        assert result.pii_entities is None

    def test_empty_channel(self):
        """Assembly handles a channel with no output gracefully."""
        stage_outputs = {
            "prepare": {"duration": 5.0, "channels": 2, "sample_rate": 16000},
            "transcribe_ch0": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}],
                "runtime": "faster-whisper",
            },
            # transcribe_ch1 is missing
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-7",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        assert len(result.segments) == 1
        assert result.segments[0].speaker == "SPEAKER_00"
        # Speakers array still has both channels
        assert len(result.speakers) == 2

    def test_explicit_pipeline_stages(self):
        """Pipeline stages can be explicitly provided."""
        stage_outputs = {
            "transcribe_ch0": {
                "text": "Hi",
                "language": "en",
                "segments": [],
                "runtime": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hey",
                "language": "en",
                "segments": [],
                "runtime": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-8",
            stage_outputs=stage_outputs,
            channel_count=2,
            pipeline_stages=["prepare", "transcribe_ch0", "transcribe_ch1"],
        )

        assert result.metadata.pipeline_stages == [
            "prepare",
            "transcribe_ch0",
            "transcribe_ch1",
        ]
