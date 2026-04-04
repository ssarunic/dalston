"""Unit tests for M68 transcript assembly module.

Tests the shared transcript assembly logic that replaces the merge engine
for linear pipelines.
"""

from dalston.common.pipeline_types import (
    AlignmentResponse,
    Segment,
    SpeakerTurn,
    Transcript,
    TranscriptSegment,
    Word,
)
from dalston.common.transcript import (
    _assign_speaker_to_word,
    _build_merged_segments,
    _extract_audio_metadata,
    _find_speaker_by_overlap,
    _find_start_turn_speaker,
    _is_sentence_ending,
    _merge_adjacent_turns,
    _merge_short_splits,
    _select_segments,
    _smooth_diarization_turns,
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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello world",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello world"},
                ],
                "engine_id": "faster-whisper",
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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello world",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello world"},
                ],
                "engine_id": "faster-whisper",
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
                "engine_id": "phoneme-align",
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

    def test_alignment_preserves_code_switching_language(self):
        """Aligned segments carry per-segment language from transcribe output."""
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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello, comment allez-vous?",
                "language": "en",
                "language_confidence": 0.7,
                "languages": [
                    {"code": "en", "confidence": 0.7, "is_primary": True},
                    {"code": "fr", "confidence": 0.3},
                ],
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "Hello,", "language": "en"},
                    {
                        "start": 1.0,
                        "end": 3.0,
                        "text": "comment allez-vous?",
                        "language": "fr",
                    },
                ],
                "engine_id": "faster-whisper",
                "timestamp_granularity": "segment",
                "alignment_method": "attention",
            },
            "align": {
                "segments": [
                    {
                        "start": 0.05,
                        "end": 0.95,
                        "text": "Hello,",
                        "language": "en",
                        "words": [
                            {
                                "text": "Hello,",
                                "start": 0.05,
                                "end": 0.95,
                                "language": "en",
                            },
                        ],
                    },
                    {
                        "start": 1.05,
                        "end": 2.9,
                        "text": "comment allez-vous?",
                        "language": "fr",
                        "words": [
                            {
                                "text": "comment",
                                "start": 1.05,
                                "end": 1.5,
                                "language": "fr",
                            },
                            {
                                "text": "allez-vous?",
                                "start": 1.6,
                                "end": 2.9,
                                "language": "fr",
                            },
                        ],
                    },
                ],
                "text": "Hello, comment allez-vous?",
                "language": "en",
                "word_timestamps": True,
                "unaligned_ratio": 0.0,
                "granularity_achieved": "word",
                "engine_id": "phoneme-align",
            },
        }

        result = assemble_transcript(
            job_id="job-align-cs",
            stage_outputs=stage_outputs,
            word_timestamps_requested=True,
        )

        assert len(result.segments) == 2
        # Aligned segments must preserve per-segment language
        assert result.segments[0].language == "en"
        assert result.segments[1].language == "fr"
        # Word-level language must also be propagated from segment
        assert result.segments[0].words is not None
        assert result.segments[0].words[0].language == "en"
        assert result.segments[1].words is not None
        for w in result.segments[1].words:
            assert w.language == "fr"
        # Transcript-level languages metadata must be present
        assert result.metadata.languages is not None
        assert len(result.metadata.languages) == 2
        assert result.metadata.languages[0].code == "en"

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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello. How are you?",
                "language": "en",
                "segments": [
                    {"start": 0.0, "end": 2.0, "text": "Hello."},
                    {"start": 3.0, "end": 5.0, "text": "How are you?"},
                ],
                "engine_id": "faster-whisper",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.5},
                    {"speaker": "SPEAKER_01", "start": 2.5, "end": 5.5},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
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

    def test_with_diarization_splits_single_segment_when_words_exist(self):
        """Diarization turns split a long transcript segment into speaker segments."""
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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello there how are you",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 5.0,
                        "text": "Hello there how are you",
                        "words": [
                            {"text": "Hello", "start": 0.0, "end": 0.6},
                            {"text": "there", "start": 0.7, "end": 1.2},
                            {"text": "how", "start": 2.6, "end": 3.0},
                            {"text": "are", "start": 3.1, "end": 3.5},
                            {"text": "you", "start": 3.6, "end": 4.0},
                        ],
                    },
                ],
                "engine_id": "faster-whisper",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.5},
                    {"speaker": "SPEAKER_01", "start": 2.5, "end": 5.0},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="job-3b",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        assert len(result.segments) == 2
        assert result.segments[0].speaker == "SPEAKER_00"
        assert result.segments[1].speaker == "SPEAKER_01"
        assert result.segments[0].text == "Hello there"
        assert result.segments[1].text == "how are you"
        assert result.segments[0].words is not None
        assert result.segments[1].words is not None

    def test_with_diarization_preserves_zero_duration_trailing_word(self):
        """Split logic should keep words where end == start (common at segment tail)."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 6.0,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "alpha beta gamma",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 3.0,
                        "text": "alpha beta gamma",
                        "words": [
                            {"text": "alpha", "start": 0.0, "end": 1.0},
                            {"text": "beta", "start": 1.2, "end": 2.2},
                            {"text": "gamma", "start": 3.0, "end": 3.0},
                        ],
                    }
                ],
                "engine_id": "faster-whisper",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 1.5},
                    {"speaker": "SPEAKER_01", "start": 1.5, "end": 3.0},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="job-3c",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        words = [
            word.text for segment in result.segments for word in (segment.words or [])
        ]
        assert words == ["alpha", "beta", "gamma"]

    def test_with_diarization_gap_does_not_create_null_speaker_word_segment(self):
        """Words crossing diarization gaps should attach to nearest/overlap speaker."""
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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "one two three four five",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 5.0,
                        "text": "one two three four five",
                        "words": [
                            {"text": "one", "start": 0.0, "end": 1.0},
                            {"text": "two", "start": 1.0, "end": 2.0},
                            {"text": "three", "start": 2.0, "end": 3.3},
                            {"text": "four", "start": 3.3, "end": 4.0},
                            {"text": "five", "start": 4.0, "end": 5.0},
                        ],
                    }
                ],
                "engine_id": "faster-whisper",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_01", "start": 0.0, "end": 2.5},
                    {"speaker": "SPEAKER_00", "start": 3.5, "end": 5.0},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="job-3d",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        assert len(result.segments) == 2
        assert result.segments[0].speaker == "SPEAKER_01"
        assert result.segments[1].speaker == "SPEAKER_00"
        assert all(seg.speaker is not None for seg in result.segments)
        assert result.segments[0].text == "one two three"
        assert result.segments[1].text == "four five"

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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}],
                "engine_id": "faster-whisper",
            },
            "diarize": {
                "turns": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.5}],
                "speakers": ["SPEAKER_00"],
                "num_speakers": 1,
                "engine_id": "pyannote-4.0",
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
                "engine_id": "faster-whisper",
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
                "engine_id": "audio-prepare",
            },
            "transcribe": {
                "text": "Test",
                "language": "en",
                "segments": [],
                "engine_id": "faster-whisper",
            },
            "align": {
                "segments": [],
                "text": "Test",
                "language": "en",
                "word_timestamps": False,
                "unaligned_ratio": 0.0,
                "granularity_achieved": "word",
                "engine_id": "phoneme-align",
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
                "engine_id": "faster-whisper",
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
                "engine_id": "faster-whisper",
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
            engine_id="faster-whisper",
        )
        align = AlignmentResponse(
            segments=[Segment(start=0.1, end=0.9, text="aligned")],
            text="aligned",
            language="en",
            word_timestamps=True,
            unaligned_ratio=0.0,
            granularity_achieved="word",
            engine_id="phoneme-align",
        )

        segments, has_words, warnings = _select_segments(
            transcript=transcript,
            align_response=align,
        )

        assert len(segments) == 1
        assert segments[0].text == "aligned"
        assert has_words is True

    def test_falls_back_to_transcribe_when_align_skipped(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="orig")],
            text="orig",
            language="en",
            engine_id="faster-whisper",
        )
        align = AlignmentResponse(
            segments=[],
            text="",
            language="en",
            word_timestamps=False,
            unaligned_ratio=0.0,
            granularity_achieved="word",
            engine_id="phoneme-align",
            skipped=True,
            skip_reason="unsupported language",
        )

        segments, has_words, warnings = _select_segments(
            transcript=transcript,
            align_response=align,
        )

        assert len(segments) == 1
        assert segments[0].text == "orig"
        assert has_words is False

    def test_uses_transcript_when_no_align(self):
        transcript = Transcript(
            segments=[TranscriptSegment(start=0.0, end=1.0, text="hello")],
            text="hello",
            language="en",
            engine_id="faster-whisper",
        )

        segments, has_words, warnings = _select_segments(
            transcript=transcript,
            align_response=None,
        )

        assert len(segments) == 1
        assert segments[0].text == "hello"
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

    def test_handles_transcript_segment_metadata(self):
        source = [
            TranscriptSegment(
                start=0.0,
                end=1.0,
                text="Hello",
                metadata={"tokens": [1, 2, 3]},
            ),
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

    def test_splits_single_segment_by_diarization_turns_without_words(self):
        source = [
            Segment(start=0.0, end=6.0, text="one two three four five six"),
        ]
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=3.0),
            SpeakerTurn(speaker="SPEAKER_01", start=3.0, end=6.0),
        ]

        result = _build_merged_segments(
            segments_source=source,
            diarization_turns=turns,
            word_timestamps_available=False,
        )

        assert len(result) == 2
        assert result[0].speaker == "SPEAKER_00"
        assert result[1].speaker == "SPEAKER_01"
        reconstructed = " ".join(
            token
            for token in f"{result[0].text} {result[1].text}".split()
            if token.strip()
        )
        assert reconstructed == "one two three four five six"


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
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hi from channel one.",
                "language": "en",
                "segments": [
                    {"start": 1.0, "end": 3.0, "text": "Hi from channel one."},
                ],
                "engine_id": "faster-whisper",
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
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "B.",
                "language": "en",
                "segments": [
                    {"start": 2.0, "end": 3.0, "text": "B."},
                ],
                "engine_id": "faster-whisper",
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
                "engine_id": "faster-whisper",
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
                "engine_id": "phoneme-align",
            },
            "transcribe_ch1": {
                "text": "World",
                "language": "en",
                "segments": [{"start": 1.0, "end": 3.0, "text": "World"}],
                "engine_id": "faster-whisper",
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
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 1.0, "end": 2.0, "text": "Hello"}],
                "engine_id": "faster-whisper",
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
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hi there.",
                "language": "en",
                "segments": [{"start": 2.0, "end": 3.0, "text": "Hi there."}],
                "engine_id": "faster-whisper",
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
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Data",
                "language": "en",
                "segments": [{"start": 1.0, "end": 2.0, "text": "Data"}],
                "engine_id": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-6",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        assert result.redacted_text is None
        assert result.pii_entities is None

    def test_per_channel_code_switching_languages(self):
        """Per-channel assembly populates metadata.languages from channel transcriptions."""
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 5.0,
                        "sample_rate": 16000,
                        "channels": 2,
                    }
                ],
            },
            "transcribe_ch0": {
                "text": "Hello there.",
                "language": "en",
                "language_confidence": 0.9,
                "languages": [
                    {"code": "en", "confidence": 0.9, "is_primary": True},
                ],
                "segments": [
                    {
                        "start": 0.0,
                        "end": 2.0,
                        "text": "Hello there.",
                        "language": "en",
                    },
                ],
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Bonjour.",
                "language": "fr",
                "language_confidence": 0.85,
                "languages": [
                    {"code": "fr", "confidence": 0.85, "is_primary": True},
                    {"code": "en", "confidence": 0.15},
                ],
                "segments": [
                    {
                        "start": 1.0,
                        "end": 3.0,
                        "text": "Bonjour.",
                        "language": "fr",
                    },
                ],
                "engine_id": "faster-whisper",
            },
        }

        result = assemble_per_channel_transcript(
            job_id="job-pc-cs",
            stage_outputs=stage_outputs,
            channel_count=2,
        )

        # metadata.languages should be populated from both channels
        assert result.metadata.languages is not None
        lang_codes = [li.code for li in result.metadata.languages]
        assert "en" in lang_codes
        assert "fr" in lang_codes
        # Sorted by confidence descending — en (0.9) > fr (0.85)
        assert result.metadata.languages[0].code == "en"
        assert result.metadata.languages[0].confidence == 0.9
        assert result.metadata.languages[1].code == "fr"
        assert result.metadata.languages[1].confidence == 0.85
        # Per-segment language should also be set
        assert result.segments[0].language == "en"
        assert result.segments[1].language == "fr"

    def test_empty_channel_raises(self):
        """Assembly raises ValueError when a channel has no output."""
        import pytest

        stage_outputs = {
            "prepare": {"duration": 5.0, "channels": 2, "sample_rate": 16000},
            "transcribe_ch0": {
                "text": "Hello",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}],
                "engine_id": "faster-whisper",
            },
            # transcribe_ch1 is missing
        }

        with pytest.raises(ValueError, match="Missing 'transcribe_ch1'"):
            assemble_per_channel_transcript(
                job_id="job-pc-7",
                stage_outputs=stage_outputs,
                channel_count=2,
            )

    def test_explicit_pipeline_stages(self):
        """Pipeline stages can be explicitly provided."""
        stage_outputs = {
            "transcribe_ch0": {
                "text": "Hi",
                "language": "en",
                "segments": [],
                "engine_id": "faster-whisper",
            },
            "transcribe_ch1": {
                "text": "Hey",
                "language": "en",
                "segments": [],
                "engine_id": "faster-whisper",
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


# ---------------------------------------------------------------------------
# _merge_adjacent_turns
# ---------------------------------------------------------------------------


class TestMergeAdjacentTurns:
    """Tests for merging consecutive same-speaker turns."""

    def test_merges_same_speaker_overlapping(self):
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=2.0),
            SpeakerTurn(speaker="A", start=1.5, end=4.0),
        ]

        result = _merge_adjacent_turns(turns)

        assert len(result) == 1
        assert result[0].speaker == "A"
        assert result[0].start == 0.0
        assert result[0].end == 4.0

    def test_merges_same_speaker_adjacent(self):
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=2.0),
            SpeakerTurn(speaker="A", start=2.0, end=3.0),
        ]

        result = _merge_adjacent_turns(turns)

        assert len(result) == 1
        assert result[0].end == 3.0

    def test_keeps_different_speakers(self):
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=2.0),
            SpeakerTurn(speaker="B", start=2.0, end=4.0),
        ]

        result = _merge_adjacent_turns(turns)

        assert len(result) == 2

    def test_empty_input(self):
        assert _merge_adjacent_turns([]) == []

    def test_single_turn(self):
        turns = [SpeakerTurn(speaker="A", start=0.0, end=1.0)]

        result = _merge_adjacent_turns(turns)

        assert len(result) == 1


# ---------------------------------------------------------------------------
# _smooth_diarization_turns
# ---------------------------------------------------------------------------


class TestSmoothDiarizationTurns:
    """Tests for micro-turn smoothing."""

    def test_removes_micro_turns_at_overlap_boundary(self):
        """Rapid alternation at speaker boundary is smoothed out."""
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=39.0, end=40.0),
            SpeakerTurn(speaker="SPEAKER_01", start=40.0, end=40.03),  # 30ms micro
            SpeakerTurn(speaker="SPEAKER_00", start=40.03, end=40.05),  # 20ms micro
            SpeakerTurn(speaker="SPEAKER_01", start=40.05, end=40.07),  # 20ms micro
            SpeakerTurn(speaker="SPEAKER_01", start=40.07, end=41.0),
        ]

        result = _smooth_diarization_turns(turns, min_duration=0.25)

        # Micro-turns should be absorbed; expect 2 clean turns.
        assert len(result) == 2
        speakers = [t.speaker for t in result]
        assert "SPEAKER_00" in speakers
        assert "SPEAKER_01" in speakers

    def test_preserves_long_turns(self):
        """Turns above the threshold are never dropped."""
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=5.0),
            SpeakerTurn(speaker="B", start=5.0, end=10.0),
        ]

        result = _smooth_diarization_turns(turns, min_duration=0.25)

        assert len(result) == 2
        assert result[0].speaker == "A"
        assert result[1].speaker == "B"

    def test_all_micro_turns_absorbed(self):
        """When all turns are micro, they still produce output."""
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=0.05),
            SpeakerTurn(speaker="B", start=0.05, end=0.1),
            SpeakerTurn(speaker="A", start=0.1, end=0.15),
        ]

        result = _smooth_diarization_turns(turns, min_duration=0.25)

        # All micro-turns — one long turn kept, others absorbed into it.
        assert len(result) >= 1
        # No turn should be lost entirely; total span is covered.
        assert result[0].start <= 0.0
        assert result[-1].end >= 0.15

    def test_no_smoothing_when_threshold_zero(self):
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=0.02),
            SpeakerTurn(speaker="B", start=0.02, end=0.04),
        ]

        result = _smooth_diarization_turns(turns, min_duration=0.0)

        assert len(result) == 2

    def test_empty_turns(self):
        assert _smooth_diarization_turns([], min_duration=0.25) == []

    def test_adjacent_same_speaker_merged_after_absorption(self):
        """After absorbing micro-turns, consecutive same-speaker turns merge."""
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=2.0),
            SpeakerTurn(speaker="B", start=2.0, end=2.05),  # micro
            SpeakerTurn(speaker="A", start=2.05, end=4.0),
        ]

        result = _smooth_diarization_turns(turns, min_duration=0.25)

        # The micro B turn gets absorbed into one of the A neighbours,
        # then the two A turns merge into one.
        assert len(result) == 1
        assert result[0].speaker == "A"
        assert result[0].start == 0.0
        assert result[0].end == 4.0

    def test_real_world_overlap_chatter(self):
        """Reproduces the actual pyannote output pattern from the bug report."""
        turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=39.248, end=39.974),
            SpeakerTurn(speaker="SPEAKER_01", start=39.974, end=40.008),  # 34ms
            SpeakerTurn(speaker="SPEAKER_00", start=40.008, end=40.025),  # 17ms
            SpeakerTurn(speaker="SPEAKER_01", start=40.025, end=40.042),  # 17ms
            SpeakerTurn(speaker="SPEAKER_00", start=40.042, end=40.058),  # 16ms
            SpeakerTurn(speaker="SPEAKER_01", start=40.058, end=40.733),
            SpeakerTurn(speaker="SPEAKER_00", start=40.092, end=41.375),
            SpeakerTurn(speaker="SPEAKER_01", start=41.375, end=43.957),
        ]

        result = _smooth_diarization_turns(turns, min_duration=0.25)

        # No micro-turns should survive.
        for turn in result:
            assert (turn.end - turn.start) >= 0.25, (
                f"Micro-turn survived: {turn.speaker} "
                f"{turn.start:.3f}-{turn.end:.3f} ({turn.end - turn.start:.3f}s)"
            )

        # All speakers should still be represented.
        speakers = {t.speaker for t in result}
        assert "SPEAKER_00" in speakers
        assert "SPEAKER_01" in speakers

    def test_integration_no_single_word_segments(self):
        """Smoothed turns should not produce single-word segments during merge."""
        source = [
            Segment(
                start=38.0,
                end=42.0,
                text="just but i think youtube",
                words=[
                    Word(text="just", start=38.0, end=38.5),
                    Word(text="but", start=39.0, end=39.5),
                    Word(text="i", start=40.0, end=40.2),
                    Word(text="think", start=40.2, end=40.8),
                    Word(text="youtube", start=41.0, end=42.0),
                ],
            ),
        ]

        # Raw micro-turns that would create single-word "i" and "think" segments.
        raw_turns = [
            SpeakerTurn(speaker="SPEAKER_00", start=37.0, end=40.0),
            SpeakerTurn(speaker="SPEAKER_01", start=40.0, end=40.03),
            SpeakerTurn(speaker="SPEAKER_00", start=40.03, end=40.05),
            SpeakerTurn(speaker="SPEAKER_01", start=40.05, end=42.5),
        ]
        smoothed = _smooth_diarization_turns(raw_turns, min_duration=0.25)

        result = _build_merged_segments(
            segments_source=source,
            diarization_turns=smoothed,
            word_timestamps_available=True,
        )

        # No segment should contain only a single word.
        for seg in result:
            word_count = len(seg.text.strip().split())
            assert word_count >= 2, (
                f"Single-word segment: '{seg.text}' speaker={seg.speaker}"
            )


# ---------------------------------------------------------------------------
# _merge_short_splits
# ---------------------------------------------------------------------------


class TestMergeShortSplits:
    """Tests for merging short split parts into neighbours."""

    def test_single_word_merged_into_larger_neighbour(self):
        parts = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello world foo",
                "speaker": "A",
                "words": [
                    Word(text="hello", start=0.0, end=0.5),
                    Word(text="world", start=0.6, end=1.0),
                    Word(text="foo", start=1.0, end=2.0),
                ],
            },
            {
                "start": 2.0,
                "end": 2.2,
                "text": "i",
                "speaker": "B",
                "words": [Word(text="i", start=2.0, end=2.2)],
            },
            {
                "start": 2.2,
                "end": 5.0,
                "text": "think yes sure",
                "speaker": "A",
                "words": [
                    Word(text="think", start=2.2, end=2.8),
                    Word(text="yes", start=3.0, end=3.5),
                    Word(text="sure", start=4.0, end=5.0),
                ],
            },
        ]

        result = _merge_short_splits(parts, min_words=3)

        # "i" should be merged into one of the neighbours.
        assert len(result) == 2
        texts = [p["text"] for p in result]
        assert all("i" in t for t in texts if len(t.split()) > 3) or len(result) == 2

    def test_preserves_parts_above_threshold(self):
        parts = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "one two three",
                "speaker": "A",
                "words": None,
            },
            {
                "start": 2.0,
                "end": 4.0,
                "text": "four five six",
                "speaker": "B",
                "words": None,
            },
        ]

        result = _merge_short_splits(parts, min_words=3)

        assert len(result) == 2

    def test_single_part_unchanged(self):
        parts = [
            {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "A", "words": None},
        ]

        result = _merge_short_splits(parts, min_words=3)

        assert len(result) == 1

    def test_two_short_parts_merge(self):
        parts = [
            {"start": 0.0, "end": 0.5, "text": "hi", "speaker": "A", "words": None},
            {"start": 0.5, "end": 1.0, "text": "there", "speaker": "B", "words": None},
        ]

        result = _merge_short_splits(parts, min_words=3)

        # Both are short but only one can absorb the other.
        assert len(result) == 1
        assert "hi" in result[0]["text"] and "there" in result[0]["text"]


# ---------------------------------------------------------------------------
# Real-world fixture: audio-2.wav transcript + diarization merge
# ---------------------------------------------------------------------------


class TestAudio2FixtureMerge:
    """Integration test using real pyannote + transcription output from audio-2.wav.

    Verifies that the full smoothing + merge pipeline produces clean segments
    with no single-word orphans.
    """

    @staticmethod
    def _load_fixture(name: str) -> dict:
        import json
        from pathlib import Path

        path = Path(__file__).parent / "fixtures" / name
        return json.loads(path.read_text())

    def test_no_single_word_segments(self):
        """Full pipeline: smooth turns then merge — no 1-word segments."""
        transcribe = self._load_fixture("audio2_transcribe.json")
        diarize = self._load_fixture("audio2_diarize.json")

        # Build typed inputs
        segments_source = []
        for seg in transcribe["segments"]:
            words = None
            if seg.get("words"):
                words = [
                    Word(text=w["text"], start=w["start"], end=w["end"])
                    for w in seg["words"]
                ]
            segments_source.append(
                Segment(
                    start=seg["start"],
                    end=seg["end"],
                    text=seg["text"],
                    words=words,
                )
            )

        raw_turns = [
            SpeakerTurn(speaker=t["speaker"], start=t["start"], end=t["end"])
            for t in diarize["turns"]
        ]

        # Apply smoothing (same as production path)
        smoothed_turns = _smooth_diarization_turns(raw_turns)

        result = _build_merged_segments(
            segments_source=segments_source,
            diarization_turns=smoothed_turns,
            word_timestamps_available=True,
        )

        # No segment should have fewer than MIN_SEGMENT_WORDS words.
        from dalston.common.transcript import MIN_SEGMENT_WORDS

        for seg in result:
            word_count = len(seg.text.strip().split())
            assert word_count >= MIN_SEGMENT_WORDS, (
                f"Short segment {seg.id}: '{seg.text}' "
                f"({word_count} words, speaker={seg.speaker}, "
                f"{seg.start:.2f}-{seg.end:.2f})"
            )

    def test_all_speakers_preserved(self):
        """Smoothing + merge should not eliminate any speaker entirely."""
        transcribe = self._load_fixture("audio2_transcribe.json")
        diarize = self._load_fixture("audio2_diarize.json")

        segments_source = []
        for seg in transcribe["segments"]:
            words = None
            if seg.get("words"):
                words = [
                    Word(text=w["text"], start=w["start"], end=w["end"])
                    for w in seg["words"]
                ]
            segments_source.append(
                Segment(
                    start=seg["start"],
                    end=seg["end"],
                    text=seg["text"],
                    words=words,
                )
            )

        raw_turns = [
            SpeakerTurn(speaker=t["speaker"], start=t["start"], end=t["end"])
            for t in diarize["turns"]
        ]

        smoothed_turns = _smooth_diarization_turns(raw_turns)

        result = _build_merged_segments(
            segments_source=segments_source,
            diarization_turns=smoothed_turns,
            word_timestamps_available=True,
        )

        result_speakers = {seg.speaker for seg in result}
        expected_speakers = set(diarize["speakers"])
        assert expected_speakers == result_speakers, (
            f"Missing speakers: {expected_speakers - result_speakers}"
        )

    def test_no_text_lost(self):
        """All words from the original transcript survive the merge."""
        transcribe = self._load_fixture("audio2_transcribe.json")
        diarize = self._load_fixture("audio2_diarize.json")

        segments_source = []
        for seg in transcribe["segments"]:
            words = None
            if seg.get("words"):
                words = [
                    Word(text=w["text"], start=w["start"], end=w["end"])
                    for w in seg["words"]
                ]
            segments_source.append(
                Segment(
                    start=seg["start"],
                    end=seg["end"],
                    text=seg["text"],
                    words=words,
                )
            )

        raw_turns = [
            SpeakerTurn(speaker=t["speaker"], start=t["start"], end=t["end"])
            for t in diarize["turns"]
        ]

        smoothed_turns = _smooth_diarization_turns(raw_turns)

        result = _build_merged_segments(
            segments_source=segments_source,
            diarization_turns=smoothed_turns,
            word_timestamps_available=True,
        )

        original_text = " ".join(seg["text"] for seg in transcribe["segments"])
        merged_text = " ".join(seg.text for seg in result)

        original_words = set(original_text.lower().split())
        merged_words = set(merged_text.lower().split())

        missing = original_words - merged_words
        assert not missing, f"Words lost during merge: {missing}"


# ---------------------------------------------------------------------------
# _is_sentence_ending
# ---------------------------------------------------------------------------


class TestIsSentenceEnding:
    def test_period(self):
        assert _is_sentence_ending("world.") is True

    def test_exclamation(self):
        assert _is_sentence_ending("stop!") is True

    def test_question(self):
        assert _is_sentence_ending("really?") is True

    def test_no_punctuation(self):
        assert _is_sentence_ending("hello") is False

    def test_comma(self):
        assert _is_sentence_ending("well,") is False

    def test_trailing_whitespace(self):
        assert _is_sentence_ending("done.  ") is True

    def test_empty(self):
        assert _is_sentence_ending("") is False


# ---------------------------------------------------------------------------
# _find_start_turn_speaker
# ---------------------------------------------------------------------------


class TestFindStartTurnSpeaker:
    def test_inside_turn(self):
        turns = [SpeakerTurn(speaker="A", start=0.0, end=5.0)]
        assert _find_start_turn_speaker(2.0, turns) == "A"

    def test_at_turn_start(self):
        turns = [SpeakerTurn(speaker="A", start=1.0, end=5.0)]
        assert _find_start_turn_speaker(1.0, turns) == "A"

    def test_at_turn_end(self):
        turns = [SpeakerTurn(speaker="A", start=1.0, end=5.0)]
        assert _find_start_turn_speaker(5.0, turns) == "A"

    def test_between_turns(self):
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=2.0),
            SpeakerTurn(speaker="B", start=3.0, end=5.0),
        ]
        assert _find_start_turn_speaker(2.5, turns) is None

    def test_empty_turns(self):
        assert _find_start_turn_speaker(1.0, []) is None


# ---------------------------------------------------------------------------
# _assign_speaker_to_word – sentence-end slip prevention
# ---------------------------------------------------------------------------


class TestAssignSpeakerToWord:
    """Tests for word-to-speaker assignment including sentence-end fix."""

    def _turns(self):
        return [
            SpeakerTurn(speaker="A", start=0.0, end=10.0),
            SpeakerTurn(speaker="B", start=10.0, end=20.0),
        ]

    def test_mid_sentence_word_uses_overlap(self):
        """A normal mid-sentence word crossing the boundary uses max overlap."""
        turns = self._turns()
        # Word "the" straddles boundary: starts at 9.8, ends at 10.6
        # Overlap with A = 0.2s, overlap with B = 0.6s → B wins
        word = Word(text="the", start=9.8, end=10.6)
        assert _assign_speaker_to_word(word, turns) == "B"

    def test_sentence_end_word_prefers_start_speaker(self):
        """A sentence-ending word that starts in A's turn stays with A,
        even when overlap with B is larger."""
        turns = self._turns()
        # "done." starts at 9.8 in A's turn, ends at 10.6 in B's turn.
        # Overlap: A=0.2s, B=0.6s → overlap would pick B.
        # But since it ends with '.', start-based picks A.
        word = Word(text="done.", start=9.8, end=10.6)
        assert _assign_speaker_to_word(word, turns) == "A"

    def test_sentence_end_exclamation(self):
        turns = self._turns()
        word = Word(text="stop!", start=9.5, end=10.8)
        assert _assign_speaker_to_word(word, turns) == "A"

    def test_sentence_end_question(self):
        turns = self._turns()
        word = Word(text="right?", start=9.9, end=10.4)
        assert _assign_speaker_to_word(word, turns) == "A"

    def test_sentence_end_fully_in_next_turn_uses_overlap(self):
        """If the sentence-ending word starts entirely in B's turn,
        start-based correctly returns B (no false correction)."""
        turns = self._turns()
        word = Word(text="yes.", start=10.2, end=10.8)
        assert _assign_speaker_to_word(word, turns) == "B"

    def test_sentence_end_fully_in_same_turn(self):
        """No boundary crossing — both strategies agree."""
        turns = self._turns()
        word = Word(text="hello.", start=3.0, end=3.5)
        assert _assign_speaker_to_word(word, turns) == "A"

    def test_mid_sentence_comma_uses_overlap(self):
        """Comma-ending word should NOT get start-based treatment."""
        turns = self._turns()
        word = Word(text="well,", start=9.8, end=10.6)
        assert _assign_speaker_to_word(word, turns) == "B"

    def test_no_turns(self):
        word = Word(text="hello.", start=1.0, end=2.0)
        assert _assign_speaker_to_word(word, []) is None

    def test_zero_duration_word(self):
        turns = self._turns()
        word = Word(text="ok.", start=5.0, end=5.0)
        assert _assign_speaker_to_word(word, turns) == "A"

    def test_invalid_word(self):
        turns = self._turns()
        word = Word(text="bad.", start=5.0, end=4.0)
        assert _assign_speaker_to_word(word, turns) is None

    def test_sentence_end_in_gap_falls_back_to_overlap(self):
        """If word start doesn't land in any turn, fall back to overlap."""
        turns = [
            SpeakerTurn(speaker="A", start=0.0, end=5.0),
            SpeakerTurn(speaker="B", start=6.0, end=10.0),
        ]
        # "end." starts in gap (5.5), more overlap with B
        word = Word(text="end.", start=5.5, end=7.0)
        assert _assign_speaker_to_word(word, turns) == "B"


# ---------------------------------------------------------------------------
# Integration: assemble_transcript with sentence-end slip scenario
# ---------------------------------------------------------------------------


class TestSentenceEndSlipIntegration:
    """End-to-end tests reproducing the sentence-end word slip bug.

    These use assemble_transcript with realistic diarization boundaries
    that fall mid-word on sentence-final words.
    """

    @staticmethod
    def _prepare_output():
        return {
            "channel_files": [
                {
                    "artifact_id": "a1",
                    "format": "wav",
                    "duration": 25.0,
                    "sample_rate": 16000,
                    "channels": 1,
                }
            ],
            "engine_id": "audio-prepare",
        }

    def test_sentence_end_word_stays_with_speaker(self):
        """'us.' should stay with SPEAKER_00, not slip to SPEAKER_01.

        Reproduces the production bug: word 'us.' starts at 9.8 in
        SPEAKER_00's turn but its end timestamp (10.6) extends past the
        diarization boundary (10.0) into SPEAKER_01's turn.
        """
        stage_outputs = {
            "prepare": self._prepare_output(),
            "transcribe": {
                "text": "doing more with us. Who is responsible",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 15.0,
                        "text": "doing more with us. Who is responsible",
                        "words": [
                            {"text": "doing", "start": 8.0, "end": 8.4},
                            {"text": "more", "start": 8.5, "end": 8.8},
                            {"text": "with", "start": 8.9, "end": 9.2},
                            {"text": "us.", "start": 9.8, "end": 10.6},
                            {"text": "Who", "start": 10.8, "end": 11.1},
                            {"text": "is", "start": 11.2, "end": 11.4},
                            {"text": "responsible", "start": 11.5, "end": 12.2},
                        ],
                    },
                ],
                "engine_id": "onnx",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0},
                    {"speaker": "SPEAKER_01", "start": 10.0, "end": 20.0},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="slip-test-1",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        # Find the segment containing "us."
        for seg in result.segments:
            if seg.words:
                word_texts = [w.text for w in seg.words]
                if "us." in word_texts:
                    assert seg.speaker == "SPEAKER_00", (
                        f"'us.' should be SPEAKER_00 but got {seg.speaker}"
                    )
                    break
        else:
            raise AssertionError("'us.' not found in any segment")

    def test_mid_sentence_word_still_uses_overlap(self):
        """Non-punctuated words crossing the boundary should still use overlap.

        'the' straddles the boundary — overlap correctly assigns it to
        SPEAKER_01 and that should not change.
        """
        stage_outputs = {
            "prepare": self._prepare_output(),
            "transcribe": {
                "text": "check the results now please.",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 15.0,
                        "text": "check the results now please.",
                        "words": [
                            {"text": "check", "start": 8.0, "end": 8.5},
                            {"text": "the", "start": 9.8, "end": 10.6},
                            {"text": "results", "start": 10.7, "end": 11.3},
                            {"text": "now", "start": 11.4, "end": 11.7},
                            {"text": "please.", "start": 11.8, "end": 12.3},
                        ],
                    },
                ],
                "engine_id": "onnx",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0},
                    {"speaker": "SPEAKER_01", "start": 10.0, "end": 20.0},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="slip-test-2",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        # "the" crosses the boundary, overlap says SPEAKER_01 — should stay
        for seg in result.segments:
            if seg.words:
                word_texts = [w.text for w in seg.words]
                if "the" in word_texts:
                    assert seg.speaker == "SPEAKER_01", (
                        f"'the' should be SPEAKER_01 (overlap) but got {seg.speaker}"
                    )
                    break

    def test_multiple_sentence_ends_at_boundaries(self):
        """Two speakers alternating with sentence-final words at each boundary."""
        stage_outputs = {
            "prepare": self._prepare_output(),
            "transcribe": {
                "text": "I agree. That sounds right. Let me think.",
                "language": "en",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 25.0,
                        "text": "I agree. That sounds right. Let me think.",
                        "words": [
                            {"text": "I", "start": 0.5, "end": 0.7},
                            {"text": "agree.", "start": 0.8, "end": 1.5},
                            # 'agree.' ends past the 1.2 boundary
                            {"text": "That", "start": 1.6, "end": 2.0},
                            {"text": "sounds", "start": 2.1, "end": 2.6},
                            {"text": "right.", "start": 2.7, "end": 3.5},
                            # 'right.' ends past the 3.0 boundary
                            {"text": "Let", "start": 3.6, "end": 3.9},
                            {"text": "me", "start": 4.0, "end": 4.2},
                            {"text": "think.", "start": 4.3, "end": 5.0},
                        ],
                    },
                ],
                "engine_id": "onnx",
            },
            "diarize": {
                "turns": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 1.2},
                    {"speaker": "SPEAKER_01", "start": 1.2, "end": 3.0},
                    {"speaker": "SPEAKER_00", "start": 3.0, "end": 6.0},
                ],
                "speakers": ["SPEAKER_00", "SPEAKER_01"],
                "num_speakers": 2,
                "engine_id": "pyannote-4.0",
            },
        }

        result = assemble_transcript(
            job_id="slip-test-3",
            stage_outputs=stage_outputs,
            speaker_detection="diarize",
        )

        # Collect speaker assignments for key words
        assignments = {}
        for seg in result.segments:
            for w in seg.words or []:
                assignments[w.text] = seg.speaker

        assert assignments["agree."] == "SPEAKER_00"
        assert assignments["right."] == "SPEAKER_01"
        assert assignments["think."] == "SPEAKER_00"
