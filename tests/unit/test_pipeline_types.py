"""Unit tests for strongly typed pipeline interface models.

Tests the data contracts between pipeline stages as specified
in dalston/common/pipeline_types.py.
"""

import pytest
from pydantic import ValidationError

from dalston.common.pipeline_types import (
    AlignmentMethod,
    AlignOutput,
    ChannelFile,
    Character,
    DiarizeOutput,
    MergedSegment,
    MergeOutput,
    Phoneme,
    PrepareOutput,
    Segment,
    Speaker,
    SpeakerDetectionMode,
    SpeakerTurn,
    SpeechRegion,
    TimestampGranularity,
    TranscribeOutput,
    TranscriptMetadata,
    Word,
)


class TestTimestampGranularityEnum:
    """Tests for TimestampGranularity enum."""

    def test_all_granularities_exist(self):
        """Test that all expected granularities are defined."""
        assert TimestampGranularity.NONE == "none"
        assert TimestampGranularity.SEGMENT == "segment"
        assert TimestampGranularity.WORD == "word"
        assert TimestampGranularity.CHARACTER == "character"
        assert TimestampGranularity.PHONEME == "phoneme"

    def test_granularity_is_string_enum(self):
        """Test that granularity values are strings."""
        for g in TimestampGranularity:
            assert isinstance(g.value, str)

    def test_granularity_from_string(self):
        """Test creating granularity from string value."""
        assert TimestampGranularity("word") == TimestampGranularity.WORD
        assert TimestampGranularity("segment") == TimestampGranularity.SEGMENT


class TestAlignmentMethodEnum:
    """Tests for AlignmentMethod enum."""

    def test_all_methods_exist(self):
        """Test that all expected alignment methods are defined."""
        assert AlignmentMethod.ATTENTION == "attention"
        assert AlignmentMethod.CTC == "ctc"
        assert AlignmentMethod.RNNT == "rnnt"
        assert AlignmentMethod.TDT == "tdt"
        assert AlignmentMethod.PHONEME_WAV2VEC == "phoneme_wav2vec"
        assert AlignmentMethod.PHONEME_MMS == "phoneme_mms"
        assert AlignmentMethod.MFA == "mfa"
        assert AlignmentMethod.WFST == "wfst"
        assert AlignmentMethod.UNKNOWN == "unknown"

    def test_method_from_string(self):
        """Test creating method from string value."""
        assert AlignmentMethod("attention") == AlignmentMethod.ATTENTION
        assert AlignmentMethod("ctc") == AlignmentMethod.CTC


class TestSpeakerDetectionModeEnum:
    """Tests for SpeakerDetectionMode enum."""

    def test_all_modes_exist(self):
        """Test that all expected modes are defined."""
        assert SpeakerDetectionMode.NONE == "none"
        assert SpeakerDetectionMode.DIARIZE == "diarize"
        assert SpeakerDetectionMode.PER_CHANNEL == "per_channel"


class TestPhonemeModel:
    """Tests for Phoneme data model."""

    def test_create_basic_phoneme(self):
        """Test creating a basic phoneme."""
        p = Phoneme(phoneme="ð", start=0.0, end=0.1)
        assert p.phoneme == "ð"
        assert p.start == 0.0
        assert p.end == 0.1
        assert p.confidence is None
        assert p.stress is None

    def test_create_full_phoneme(self):
        """Test creating phoneme with all fields."""
        p = Phoneme(phoneme="ə", start=0.1, end=0.2, confidence=0.95, stress=1)
        assert p.phoneme == "ə"
        assert p.confidence == 0.95
        assert p.stress == 1

    def test_phoneme_rejects_negative_start(self):
        """Test that negative start time is rejected."""
        with pytest.raises(ValidationError):
            Phoneme(phoneme="a", start=-0.1, end=0.1)

    def test_phoneme_rejects_invalid_stress(self):
        """Test that invalid stress values are rejected."""
        with pytest.raises(ValidationError):
            Phoneme(phoneme="a", start=0.0, end=0.1, stress=3)

    def test_phoneme_forbids_extra_fields(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError):
            Phoneme(phoneme="a", start=0.0, end=0.1, extra_field="invalid")


class TestCharacterModel:
    """Tests for Character data model."""

    def test_create_basic_character(self):
        """Test creating a basic character."""
        c = Character(char="H", start=0.0, end=0.05)
        assert c.char == "H"
        assert c.start == 0.0
        assert c.end == 0.05

    def test_character_with_phonemes(self):
        """Test character with source phonemes."""
        phonemes = [
            Phoneme(phoneme="h", start=0.0, end=0.03),
            Phoneme(phoneme="æ", start=0.03, end=0.05),
        ]
        c = Character(char="H", start=0.0, end=0.05, phonemes=phonemes)
        assert len(c.phonemes) == 2

    def test_character_rejects_multi_char(self):
        """Test that multi-character strings are rejected."""
        with pytest.raises(ValidationError):
            Character(char="ab", start=0.0, end=0.1)


class TestWordModel:
    """Tests for Word data model."""

    def test_create_basic_word(self):
        """Test creating a basic word."""
        w = Word(text="hello", start=0.0, end=0.5)
        assert w.text == "hello"
        assert w.start == 0.0
        assert w.end == 0.5
        assert w.confidence is None
        assert w.characters is None
        assert w.phonemes is None

    def test_create_word_with_confidence(self):
        """Test creating word with confidence score."""
        w = Word(text="world", start=0.5, end=1.0, confidence=0.98)
        assert w.confidence == 0.98

    def test_create_word_with_alignment_method(self):
        """Test creating word with alignment method."""
        w = Word(
            text="test",
            start=0.0,
            end=0.3,
            alignment_method=AlignmentMethod.ATTENTION,
        )
        assert w.alignment_method == AlignmentMethod.ATTENTION

    def test_word_with_characters(self):
        """Test word with character-level timing."""
        chars = [
            Character(char="h", start=0.0, end=0.1),
            Character(char="i", start=0.1, end=0.2),
        ]
        w = Word(text="hi", start=0.0, end=0.2, characters=chars)
        assert len(w.characters) == 2


class TestSegmentModel:
    """Tests for Segment data model."""

    def test_create_basic_segment(self):
        """Test creating a basic segment."""
        s = Segment(start=0.0, end=5.0, text="Hello world")
        assert s.start == 0.0
        assert s.end == 5.0
        assert s.text == "Hello world"
        assert s.id is None
        assert s.words is None

    def test_create_segment_with_id(self):
        """Test creating segment with ID."""
        s = Segment(id="seg_001", start=0.0, end=5.0, text="Test")
        assert s.id == "seg_001"

    def test_segment_with_words(self):
        """Test segment with word-level detail."""
        words = [
            Word(text="Hello", start=0.0, end=0.5),
            Word(text="world", start=0.5, end=1.0),
        ]
        s = Segment(start=0.0, end=1.0, text="Hello world", words=words)
        assert len(s.words) == 2

    def test_segment_with_language(self):
        """Test segment with language code (code-switching)."""
        s = Segment(start=0.0, end=1.0, text="Bonjour", language="fr")
        assert s.language == "fr"

    def test_segment_speech_flag(self):
        """Test segment with is_speech flag."""
        s = Segment(start=0.0, end=2.0, text="[music]", is_speech=False)
        assert s.is_speech is False


class TestSpeakerTurnModel:
    """Tests for SpeakerTurn data model."""

    def test_create_basic_turn(self):
        """Test creating a basic speaker turn."""
        t = SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.0)
        assert t.speaker == "SPEAKER_00"
        assert t.start == 0.0
        assert t.end == 5.0

    def test_turn_with_confidence(self):
        """Test speaker turn with confidence."""
        t = SpeakerTurn(speaker="SPEAKER_01", start=5.0, end=10.0, confidence=0.92)
        assert t.confidence == 0.92

    def test_turn_with_overlap(self):
        """Test speaker turn with overlapping speakers."""
        t = SpeakerTurn(
            speaker="SPEAKER_00",
            start=5.0,
            end=7.0,
            overlapping_speakers=["SPEAKER_01"],
        )
        assert t.overlapping_speakers == ["SPEAKER_01"]


class TestSpeechRegionModel:
    """Tests for SpeechRegion data model."""

    def test_create_speech_region(self):
        """Test creating a speech region."""
        r = SpeechRegion(start=1.0, end=5.0, confidence=0.99)
        assert r.start == 1.0
        assert r.end == 5.0
        assert r.confidence == 0.99


class TestChannelFileModel:
    """Tests for ChannelFile data model."""

    def test_create_channel_file(self):
        """Test creating a channel file entry."""
        cf = ChannelFile(
            channel=0,
            audio_uri="s3://bucket/jobs/123/audio/prepared_ch0.wav",
            duration=60.5,
        )
        assert cf.channel == 0
        assert "prepared_ch0.wav" in cf.audio_uri
        assert cf.duration == 60.5


class TestSpeakerModel:
    """Tests for Speaker data model."""

    def test_create_basic_speaker(self):
        """Test creating a basic speaker."""
        s = Speaker(id="SPEAKER_00")
        assert s.id == "SPEAKER_00"
        assert s.label is None
        assert s.channel is None

    def test_create_speaker_with_channel(self):
        """Test creating speaker with channel assignment."""
        s = Speaker(id="SPEAKER_00", label="Agent", channel=0)
        assert s.label == "Agent"
        assert s.channel == 0


class TestMergedSegmentModel:
    """Tests for MergedSegment data model."""

    def test_create_merged_segment(self):
        """Test creating a merged segment."""
        ms = MergedSegment(
            id="seg_000",
            start=0.0,
            end=5.0,
            text="Hello world",
            speaker="SPEAKER_00",
        )
        assert ms.id == "seg_000"
        assert ms.speaker == "SPEAKER_00"
        assert ms.events == []

    def test_merged_segment_with_emotion(self):
        """Test merged segment with emotion detection."""
        ms = MergedSegment(
            id="seg_001",
            start=5.0,
            end=10.0,
            text="That's great!",
            emotion="happy",
            emotion_confidence=0.85,
        )
        assert ms.emotion == "happy"
        assert ms.emotion_confidence == 0.85


class TestPrepareOutput:
    """Tests for PrepareOutput stage model."""

    def test_create_basic_prepare_output(self):
        """Test creating basic prepare output."""
        out = PrepareOutput(
            audio_uri="s3://bucket/jobs/123/audio/prepared.wav",
            duration=60.0,
            sample_rate=16000,
            channels=1,
            engine_id="audio-prepare",
        )
        assert out.audio_uri is not None
        assert out.duration == 60.0
        assert out.sample_rate == 16000
        assert out.channels == 1
        assert out.skipped is False
        assert out.warnings == []

    def test_prepare_output_with_original_metadata(self):
        """Test prepare output with original audio metadata."""
        out = PrepareOutput(
            audio_uri="s3://bucket/audio.wav",
            duration=60.0,
            sample_rate=16000,
            channels=1,
            original_format="mp3",
            original_duration=60.5,
            original_sample_rate=44100,
            original_channels=2,
            engine_id="audio-prepare",
        )
        assert out.original_format == "mp3"
        assert out.original_sample_rate == 44100
        assert out.original_channels == 2

    def test_prepare_output_split_channels(self):
        """Test prepare output with split channels."""
        out = PrepareOutput(
            channel_uris=[
                "s3://bucket/ch0.wav",
                "s3://bucket/ch1.wav",
            ],
            channel_files=[
                ChannelFile(channel=0, audio_uri="s3://bucket/ch0.wav", duration=60.0),
                ChannelFile(channel=1, audio_uri="s3://bucket/ch1.wav", duration=60.0),
            ],
            channel_count=2,
            split_channels=True,
            duration=60.0,
            sample_rate=16000,
            channels=1,
            engine_id="audio-prepare",
        )
        assert out.split_channels is True
        assert out.channel_count == 2
        assert len(out.channel_files) == 2

    def test_prepare_output_with_vad(self):
        """Test prepare output with VAD regions."""
        regions = [
            SpeechRegion(start=1.0, end=5.0),
            SpeechRegion(start=10.0, end=25.0),
        ]
        out = PrepareOutput(
            audio_uri="s3://bucket/audio.wav",
            duration=60.0,
            sample_rate=16000,
            channels=1,
            speech_regions=regions,
            speech_ratio=0.32,
            engine_id="audio-prepare",
        )
        assert len(out.speech_regions) == 2
        assert out.speech_ratio == 0.32


class TestTranscribeOutput:
    """Tests for TranscribeOutput stage model."""

    def test_create_basic_transcribe_output(self):
        """Test creating basic transcribe output."""
        out = TranscribeOutput(
            segments=[Segment(start=0.0, end=5.0, text="Hello world")],
            text="Hello world",
            language="en",
            engine_id="faster-whisper",
        )
        assert len(out.segments) == 1
        assert out.text == "Hello world"
        assert out.language == "en"
        assert out.skipped is False

    def test_transcribe_output_with_word_timestamps(self):
        """Test transcribe output with word-level timestamps."""
        words = [
            Word(text="Hello", start=0.0, end=0.5, confidence=0.98),
            Word(text="world", start=0.5, end=1.0, confidence=0.99),
        ]
        out = TranscribeOutput(
            segments=[Segment(start=0.0, end=1.0, text="Hello world", words=words)],
            text="Hello world",
            language="en",
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=TimestampGranularity.WORD,
            alignment_method=AlignmentMethod.ATTENTION,
            engine_id="faster-whisper",
        )
        assert out.timestamp_granularity_actual == TimestampGranularity.WORD
        assert out.alignment_method == AlignmentMethod.ATTENTION

    def test_transcribe_output_with_language_confidence(self):
        """Test transcribe output with language detection confidence."""
        out = TranscribeOutput(
            segments=[Segment(start=0.0, end=1.0, text="Bonjour")],
            text="Bonjour",
            language="fr",
            language_confidence=0.97,
            engine_id="faster-whisper",
        )
        assert out.language == "fr"
        assert out.language_confidence == 0.97


class TestAlignOutput:
    """Tests for AlignOutput stage model."""

    def test_create_basic_align_output(self):
        """Test creating basic align output."""
        out = AlignOutput(
            segments=[Segment(start=0.0, end=1.0, text="Hello")],
            text="Hello",
            language="en",
            word_timestamps=True,
            engine_id="whisperx-align",
        )
        assert out.word_timestamps is True
        assert out.skipped is False

    def test_align_output_with_statistics(self):
        """Test align output with alignment statistics."""
        out = AlignOutput(
            segments=[Segment(start=0.0, end=1.0, text="Hello world")],
            text="Hello world",
            language="en",
            word_timestamps=True,
            alignment_confidence=0.92,
            unaligned_words=["unfamiliarterm"],
            unaligned_ratio=0.05,
            granularity_achieved=TimestampGranularity.WORD,
            engine_id="whisperx-align",
        )
        assert out.alignment_confidence == 0.92
        assert out.unaligned_ratio == 0.05
        assert "unfamiliarterm" in out.unaligned_words

    def test_align_output_skipped(self):
        """Test align output when skipped."""
        out = AlignOutput(
            segments=[Segment(start=0.0, end=1.0, text="Hello")],
            text="Hello",
            language="xx",
            word_timestamps=False,
            engine_id="whisperx-align",
            skipped=True,
            skip_reason="No alignment model for language 'xx'",
            warnings=["No alignment model for language 'xx'"],
        )
        assert out.skipped is True
        assert out.skip_reason is not None
        assert len(out.warnings) == 1


class TestDiarizeOutput:
    """Tests for DiarizeOutput stage model."""

    def test_create_basic_diarize_output(self):
        """Test creating basic diarize output."""
        out = DiarizeOutput(
            turns=[
                SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.0),
                SpeakerTurn(speaker="SPEAKER_01", start=5.0, end=10.0),
            ],
            speakers=["SPEAKER_00", "SPEAKER_01"],
            num_speakers=2,
            engine_id="pyannote-3.1",
        )
        assert out.num_speakers == 2
        assert len(out.turns) == 2
        assert "SPEAKER_00" in out.speakers

    def test_diarize_output_with_overlap(self):
        """Test diarize output with overlap statistics."""
        out = DiarizeOutput(
            turns=[
                SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.0),
                SpeakerTurn(
                    speaker="SPEAKER_01",
                    start=4.5,
                    end=10.0,
                    overlapping_speakers=["SPEAKER_00"],
                ),
            ],
            speakers=["SPEAKER_00", "SPEAKER_01"],
            num_speakers=2,
            overlap_duration=0.5,
            overlap_ratio=0.05,
            engine_id="pyannote-3.1",
        )
        assert out.overlap_duration == 0.5
        assert out.overlap_ratio == 0.05


class TestMergeOutput:
    """Tests for MergeOutput stage model."""

    def test_create_basic_merge_output(self):
        """Test creating basic merge output."""
        metadata = TranscriptMetadata(
            audio_duration=60.0,
            audio_channels=1,
            sample_rate=16000,
            language="en",
            created_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:01:00Z",
        )
        out = MergeOutput(
            job_id="job-123",
            metadata=metadata,
            text="Hello world",
            segments=[
                MergedSegment(id="seg_000", start=0.0, end=1.0, text="Hello world")
            ],
        )
        assert out.job_id == "job-123"
        assert out.version == "1.0"
        assert len(out.segments) == 1

    def test_merge_output_with_speakers(self):
        """Test merge output with speaker assignments."""
        metadata = TranscriptMetadata(
            audio_duration=60.0,
            audio_channels=1,
            sample_rate=16000,
            language="en",
            speaker_detection=SpeakerDetectionMode.DIARIZE,
            speaker_count=2,
            created_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:01:00Z",
        )
        out = MergeOutput(
            job_id="job-123",
            metadata=metadata,
            text="Hello. Hi there.",
            speakers=[
                Speaker(id="SPEAKER_00"),
                Speaker(id="SPEAKER_01"),
            ],
            segments=[
                MergedSegment(
                    id="seg_000",
                    start=0.0,
                    end=1.0,
                    text="Hello.",
                    speaker="SPEAKER_00",
                ),
                MergedSegment(
                    id="seg_001",
                    start=1.0,
                    end=2.0,
                    text="Hi there.",
                    speaker="SPEAKER_01",
                ),
            ],
        )
        assert len(out.speakers) == 2
        assert out.segments[0].speaker == "SPEAKER_00"
        assert out.segments[1].speaker == "SPEAKER_01"


class TestTranscriptMetadata:
    """Tests for TranscriptMetadata model."""

    def test_create_basic_metadata(self):
        """Test creating basic transcript metadata."""
        meta = TranscriptMetadata(
            audio_duration=60.0,
            audio_channels=1,
            sample_rate=16000,
            language="en",
            created_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:01:00Z",
        )
        assert meta.audio_duration == 60.0
        assert meta.language == "en"
        assert meta.word_timestamps is False
        assert meta.speaker_detection == SpeakerDetectionMode.NONE

    def test_metadata_with_word_timestamps(self):
        """Test metadata with word timestamps flag."""
        meta = TranscriptMetadata(
            audio_duration=60.0,
            audio_channels=1,
            sample_rate=16000,
            language="en",
            word_timestamps=True,
            word_timestamps_requested=True,
            created_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:01:00Z",
        )
        assert meta.word_timestamps is True
        assert meta.word_timestamps_requested is True

    def test_metadata_with_pipeline_stages(self):
        """Test metadata with pipeline stages list."""
        meta = TranscriptMetadata(
            audio_duration=60.0,
            audio_channels=1,
            sample_rate=16000,
            language="en",
            pipeline_stages=["prepare", "transcribe", "align", "merge"],
            created_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:01:00Z",
        )
        assert "align" in meta.pipeline_stages
        assert len(meta.pipeline_stages) == 4


class TestModelSerialization:
    """Tests for model serialization to JSON."""

    def test_segment_to_json(self):
        """Test segment serializes to JSON correctly."""
        s = Segment(
            start=0.0,
            end=1.0,
            text="Hello",
            words=[Word(text="Hello", start=0.0, end=1.0)],
        )
        data = s.model_dump(mode="json")
        assert data["start"] == 0.0
        assert data["text"] == "Hello"
        assert len(data["words"]) == 1

    def test_transcribe_output_to_json(self):
        """Test TranscribeOutput serializes correctly."""
        out = TranscribeOutput(
            segments=[Segment(start=0.0, end=1.0, text="Hello")],
            text="Hello",
            language="en",
            timestamp_granularity_actual=TimestampGranularity.WORD,
            engine_id="test",
        )
        data = out.model_dump(mode="json")
        assert data["language"] == "en"
        assert data["timestamp_granularity_actual"] == "word"
        assert data["skipped"] is False

    def test_diarize_output_to_json(self):
        """Test DiarizeOutput serializes correctly."""
        out = DiarizeOutput(
            turns=[SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=5.0)],
            speakers=["SPEAKER_00"],
            num_speakers=1,
            engine_id="test",
        )
        data = out.model_dump(mode="json")
        assert data["num_speakers"] == 1
        assert data["turns"][0]["speaker"] == "SPEAKER_00"


class TestModelValidation:
    """Tests for model validation constraints."""

    def test_segment_rejects_negative_timestamps(self):
        """Test that segments reject negative timestamps."""
        with pytest.raises(ValidationError):
            Segment(start=-1.0, end=1.0, text="Hello")

    def test_word_confidence_must_be_valid(self):
        """Test that word confidence is validated."""
        # Valid confidence
        w = Word(text="hello", start=0.0, end=1.0, confidence=0.5)
        assert w.confidence == 0.5

    def test_diarize_output_speaker_count_non_negative(self):
        """Test that speaker count must be non-negative."""
        with pytest.raises(ValidationError):
            DiarizeOutput(
                turns=[],
                speakers=[],
                num_speakers=-1,
                engine_id="test",
            )

    def test_prepare_output_sample_rate_positive(self):
        """Test that sample rate must be positive."""
        with pytest.raises(ValidationError):
            PrepareOutput(
                audio_uri="s3://test",
                duration=60.0,
                sample_rate=0,
                channels=1,
                engine_id="test",
            )
