"""Unit tests for realtime_sdk assembler module."""

import pytest

from dalston.common.pipeline_types import Transcript, TranscriptSegment, TranscriptWord
from dalston.realtime_sdk.assembler import (
    Segment,
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


def _make_transcript(
    text: str,
    words: list[Word] | None = None,
    language: str = "en",
    confidence: float | None = None,
) -> Transcript:
    """Build a Transcript from assembler-style Word objects for test convenience."""
    tw_list: list[TranscriptWord] = []
    if words:
        tw_list = [
            TranscriptWord(
                text=w.word, start=w.start, end=w.end, confidence=w.confidence
            )
            for w in words
        ]
    seg_start = tw_list[0].start if tw_list else 0.0
    seg_end = tw_list[-1].end if tw_list else 0.0
    segments = (
        [
            TranscriptSegment(
                start=seg_start,
                end=seg_end,
                text=text,
                words=tw_list if tw_list else None,
                confidence=confidence,
            )
        ]
        if text
        else []
    )
    return Transcript(
        text=text,
        segments=segments,
        language=language,
        engine_id="test",
    )


class TestTranscript:
    """Tests for Transcript model (replacement for TranscribeResult)."""

    def test_create_transcript(self):
        transcript = _make_transcript(
            text="hello world",
            words=[
                Word(word="hello", start=0.0, end=0.5, confidence=0.95),
                Word(word="world", start=0.6, end=1.0, confidence=0.90),
            ],
            language="en",
            confidence=0.92,
        )

        assert transcript.text == "hello world"
        assert len(transcript.segments) == 1
        assert len(transcript.segments[0].words) == 2
        assert transcript.language == "en"
        assert transcript.segments[0].confidence == 0.92


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
        result = _make_transcript(
            text="Hello",
            words=[Word(word="Hello", start=0.0, end=0.5, confidence=0.95)],
            confidence=0.95,
        )

        segment = assembler.add_transcript(result, audio_duration=2.0)

        assert segment.id == "seg_0000"
        assert segment.start == 0.0
        assert segment.end == 2.0
        assert segment.text == "Hello"
        assert assembler.current_time == 2.0
        assert assembler.segment_count == 1

    def test_add_multiple_utterances(self, assembler: TranscriptAssembler):
        # First utterance
        result1 = _make_transcript(
            text="Hello",
            words=[Word(word="Hello", start=0.0, end=0.5, confidence=0.95)],
            confidence=0.95,
        )
        seg1 = assembler.add_transcript(result1, audio_duration=2.0)

        # Second utterance
        result2 = _make_transcript(
            text="World",
            words=[Word(word="World", start=0.0, end=0.6, confidence=0.90)],
            confidence=0.90,
        )
        seg2 = assembler.add_transcript(result2, audio_duration=1.5)

        assert seg1.start == 0.0
        assert seg1.end == 2.0
        assert seg2.start == 2.0
        assert seg2.end == 3.5
        assert assembler.current_time == 3.5
        assert assembler.segment_count == 2

    def test_word_timestamps_adjusted(self, assembler: TranscriptAssembler):
        # First utterance with words
        result1 = _make_transcript(
            text="Hello world",
            words=[
                Word(word="Hello", start=0.0, end=0.5, confidence=0.95),
                Word(word="world", start=0.6, end=1.0, confidence=0.90),
            ],
            confidence=0.92,
        )
        assembler.add_transcript(result1, audio_duration=2.0)

        # Second utterance - words start at 0.0 in the audio segment
        result2 = _make_transcript(
            text="How are you",
            words=[
                Word(word="How", start=0.0, end=0.3, confidence=0.88),
                Word(word="are", start=0.4, end=0.6, confidence=0.85),
                Word(word="you", start=0.7, end=1.0, confidence=0.90),
            ],
            confidence=0.87,
        )
        seg2 = assembler.add_transcript(result2, audio_duration=1.5)

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
        assembler.add_transcript(
            _make_transcript(text="Hello", confidence=0.9),
            audio_duration=1.0,
        )
        assembler.add_transcript(
            _make_transcript(text="world", confidence=0.9),
            audio_duration=1.0,
        )
        assembler.add_transcript(
            _make_transcript(text="test", confidence=0.9),
            audio_duration=1.0,
        )

        transcript = assembler.get_full_transcript()

        assert transcript == "Hello world test"

    def test_get_full_transcript_skips_empty(self, assembler: TranscriptAssembler):
        assembler.add_transcript(
            _make_transcript(text="Hello", confidence=0.9),
            audio_duration=1.0,
        )
        assembler.add_transcript(
            _make_transcript(text="", confidence=0.9),
            audio_duration=0.5,
        )
        assembler.add_transcript(
            _make_transcript(text="world", confidence=0.9),
            audio_duration=1.0,
        )

        transcript = assembler.get_full_transcript()

        assert transcript == "Hello world"

    def test_get_segments_returns_copy(self, assembler: TranscriptAssembler):
        assembler.add_transcript(
            _make_transcript(text="Test", confidence=0.9),
            audio_duration=1.0,
        )

        segments1 = assembler.get_segments()
        segments2 = assembler.get_segments()

        assert segments1 == segments2
        assert segments1 is not segments2  # Should be a copy

    def test_reset(self, assembler: TranscriptAssembler):
        # Add some utterances
        assembler.add_transcript(
            _make_transcript(text="Hello", confidence=0.9),
            audio_duration=2.0,
        )
        assembler.add_transcript(
            _make_transcript(text="World", confidence=0.9),
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
            seg = assembler.add_transcript(
                _make_transcript(text=f"seg{i}", confidence=0.9),
                audio_duration=1.0,
            )
            assert seg.id == f"seg_{i:04d}"

    def test_empty_words_list(self, assembler: TranscriptAssembler):
        result = _make_transcript(
            text="No words",
            confidence=0.8,
        )

        segment = assembler.add_transcript(result, audio_duration=1.0)

        assert segment.words == []
        assert segment.text == "No words"

    def test_confidence_preserved(self, assembler: TranscriptAssembler):
        result = _make_transcript(
            text="Test",
            confidence=0.87,
        )

        segment = assembler.add_transcript(result, audio_duration=1.0)

        assert segment.confidence == 0.87
