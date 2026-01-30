"""Unit tests for realtime_sdk assembler module."""

import pytest

from dalston.realtime_sdk.assembler import (
    Segment,
    TranscribeResult,
    TranscriptAssembler,
    Word,
)


class TestWord:
    """Tests for Word dataclass."""

    def test_create_word(self):
        word = Word(word="hello", start=0.0, end=0.5, confidence=0.95)

        assert word.word == "hello"
        assert word.start == 0.0
        assert word.end == 0.5
        assert word.confidence == 0.95


class TestSegment:
    """Tests for Segment dataclass."""

    def test_create_segment(self):
        words = [Word(word="hello", start=0.0, end=0.5, confidence=0.95)]
        segment = Segment(
            id="seg_0001",
            start=0.0,
            end=1.0,
            text="hello",
            words=words,
            confidence=0.95,
        )

        assert segment.id == "seg_0001"
        assert segment.start == 0.0
        assert segment.end == 1.0
        assert segment.text == "hello"
        assert len(segment.words) == 1
        assert segment.confidence == 0.95


class TestTranscribeResult:
    """Tests for TranscribeResult dataclass."""

    def test_create_result(self):
        words = [
            Word(word="hello", start=0.0, end=0.5, confidence=0.95),
            Word(word="world", start=0.6, end=1.0, confidence=0.90),
        ]
        result = TranscribeResult(
            text="hello world",
            words=words,
            language="en",
            confidence=0.92,
        )

        assert result.text == "hello world"
        assert len(result.words) == 2
        assert result.language == "en"
        assert result.confidence == 0.92


class TestTranscriptAssembler:
    """Tests for TranscriptAssembler class."""

    @pytest.fixture
    def assembler(self) -> TranscriptAssembler:
        return TranscriptAssembler()

    def test_initial_state(self, assembler: TranscriptAssembler):
        assert assembler.current_time == 0.0
        assert assembler.segment_count == 0
        assert assembler.get_full_transcript() == ""
        assert assembler.get_segments() == []

    def test_add_single_utterance(self, assembler: TranscriptAssembler):
        result = TranscribeResult(
            text="Hello",
            words=[Word(word="Hello", start=0.0, end=0.5, confidence=0.95)],
            language="en",
            confidence=0.95,
        )

        segment = assembler.add_utterance(result, audio_duration=2.0)

        assert segment.id == "seg_0000"
        assert segment.start == 0.0
        assert segment.end == 2.0
        assert segment.text == "Hello"
        assert assembler.current_time == 2.0
        assert assembler.segment_count == 1

    def test_add_multiple_utterances(self, assembler: TranscriptAssembler):
        # First utterance
        result1 = TranscribeResult(
            text="Hello",
            words=[Word(word="Hello", start=0.0, end=0.5, confidence=0.95)],
            language="en",
            confidence=0.95,
        )
        seg1 = assembler.add_utterance(result1, audio_duration=2.0)

        # Second utterance
        result2 = TranscribeResult(
            text="World",
            words=[Word(word="World", start=0.0, end=0.6, confidence=0.90)],
            language="en",
            confidence=0.90,
        )
        seg2 = assembler.add_utterance(result2, audio_duration=1.5)

        assert seg1.start == 0.0
        assert seg1.end == 2.0
        assert seg2.start == 2.0
        assert seg2.end == 3.5
        assert assembler.current_time == 3.5
        assert assembler.segment_count == 2

    def test_word_timestamps_adjusted(self, assembler: TranscriptAssembler):
        # First utterance with words
        result1 = TranscribeResult(
            text="Hello world",
            words=[
                Word(word="Hello", start=0.0, end=0.5, confidence=0.95),
                Word(word="world", start=0.6, end=1.0, confidence=0.90),
            ],
            language="en",
            confidence=0.92,
        )
        assembler.add_utterance(result1, audio_duration=2.0)

        # Second utterance - words start at 0.0 in the audio segment
        result2 = TranscribeResult(
            text="How are you",
            words=[
                Word(word="How", start=0.0, end=0.3, confidence=0.88),
                Word(word="are", start=0.4, end=0.6, confidence=0.85),
                Word(word="you", start=0.7, end=1.0, confidence=0.90),
            ],
            language="en",
            confidence=0.87,
        )
        seg2 = assembler.add_utterance(result2, audio_duration=1.5)

        # Words should be adjusted to session timeline (start at 2.0)
        assert seg2.words[0].word == "How"
        assert seg2.words[0].start == 2.0  # 0.0 + 2.0
        assert seg2.words[0].end == 2.3  # 0.3 + 2.0

        assert seg2.words[1].word == "are"
        assert seg2.words[1].start == 2.4  # 0.4 + 2.0
        assert seg2.words[1].end == 2.6  # 0.6 + 2.0

        assert seg2.words[2].word == "you"
        assert seg2.words[2].start == 2.7  # 0.7 + 2.0
        assert seg2.words[2].end == 3.0  # 1.0 + 2.0

    def test_get_full_transcript(self, assembler: TranscriptAssembler):
        assembler.add_utterance(
            TranscribeResult(text="Hello", words=[], language="en", confidence=0.9),
            audio_duration=1.0,
        )
        assembler.add_utterance(
            TranscribeResult(text="world", words=[], language="en", confidence=0.9),
            audio_duration=1.0,
        )
        assembler.add_utterance(
            TranscribeResult(text="test", words=[], language="en", confidence=0.9),
            audio_duration=1.0,
        )

        transcript = assembler.get_full_transcript()

        assert transcript == "Hello world test"

    def test_get_full_transcript_skips_empty(self, assembler: TranscriptAssembler):
        assembler.add_utterance(
            TranscribeResult(text="Hello", words=[], language="en", confidence=0.9),
            audio_duration=1.0,
        )
        assembler.add_utterance(
            TranscribeResult(text="", words=[], language="en", confidence=0.9),
            audio_duration=0.5,
        )
        assembler.add_utterance(
            TranscribeResult(text="world", words=[], language="en", confidence=0.9),
            audio_duration=1.0,
        )

        transcript = assembler.get_full_transcript()

        assert transcript == "Hello world"

    def test_get_segments_returns_copy(self, assembler: TranscriptAssembler):
        assembler.add_utterance(
            TranscribeResult(text="Test", words=[], language="en", confidence=0.9),
            audio_duration=1.0,
        )

        segments1 = assembler.get_segments()
        segments2 = assembler.get_segments()

        assert segments1 == segments2
        assert segments1 is not segments2  # Should be a copy

    def test_reset(self, assembler: TranscriptAssembler):
        # Add some utterances
        assembler.add_utterance(
            TranscribeResult(text="Hello", words=[], language="en", confidence=0.9),
            audio_duration=2.0,
        )
        assembler.add_utterance(
            TranscribeResult(text="World", words=[], language="en", confidence=0.9),
            audio_duration=1.5,
        )

        assert assembler.segment_count == 2
        assert assembler.current_time == 3.5

        # Reset
        assembler.reset()

        assert assembler.segment_count == 0
        assert assembler.current_time == 0.0
        assert assembler.get_full_transcript() == ""
        assert assembler.get_segments() == []

    def test_segment_id_format(self, assembler: TranscriptAssembler):
        for i in range(5):
            seg = assembler.add_utterance(
                TranscribeResult(text=f"seg{i}", words=[], language="en", confidence=0.9),
                audio_duration=1.0,
            )
            assert seg.id == f"seg_{i:04d}"

    def test_empty_words_list(self, assembler: TranscriptAssembler):
        result = TranscribeResult(
            text="No words",
            words=[],
            language="en",
            confidence=0.8,
        )

        segment = assembler.add_utterance(result, audio_duration=1.0)

        assert segment.words == []
        assert segment.text == "No words"

    def test_confidence_preserved(self, assembler: TranscriptAssembler):
        result = TranscribeResult(
            text="Test",
            words=[],
            language="en",
            confidence=0.87,
        )

        segment = assembler.add_utterance(result, audio_duration=1.0)

        assert segment.confidence == 0.87
