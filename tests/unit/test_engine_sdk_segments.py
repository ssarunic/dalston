"""Tests for dalston.engine_sdk.segments (shared words-to-segments rule)."""

from __future__ import annotations

from dataclasses import dataclass, field

from dalston.engine_sdk.segments import is_sentence_ending, words_to_segments


@dataclass
class _Word:
    text: str
    start: float
    end: float


@dataclass
class _Segment:
    start: float
    end: float
    text: str
    words: list[_Word] = field(default_factory=list)


def _group(words: list[_Word], full_text: str = "") -> list[_Segment]:
    return words_to_segments(
        words,
        word_text=lambda w: w.text,
        word_start=lambda w: w.start,
        word_end=lambda w: w.end,
        make_segment=lambda s, e, t, ws: _Segment(s, e, t, ws),
        full_text=full_text,
    )


class TestIsSentenceEnding:
    def test_latin_punctuation(self) -> None:
        assert is_sentence_ending("done.")
        assert is_sentence_ending("really?")
        assert is_sentence_ending("wow!")
        assert is_sentence_ending("trailing. ")

    def test_cjk_punctuation(self) -> None:
        assert is_sentence_ending("完了。")
        assert is_sentence_ending("吗？")

    def test_non_ending(self) -> None:
        assert not is_sentence_ending("hello")
        assert not is_sentence_ending("Dr.Who,")
        assert not is_sentence_ending("")


class TestWordsToSegments:
    def test_splits_on_sentence_endings(self) -> None:
        words = [
            _Word("Hello", 0.0, 0.3),
            _Word("world.", 0.4, 0.8),
            _Word("How", 1.0, 1.2),
            _Word("are", 1.3, 1.4),
            _Word("you?", 1.5, 1.9),
            _Word("Fine", 2.0, 2.4),
        ]
        segments = _group(words)
        assert [s.text for s in segments] == ["Hello world.", "How are you?", "Fine"]
        assert (segments[0].start, segments[0].end) == (0.0, 0.8)
        assert (segments[1].start, segments[1].end) == (1.0, 1.9)
        assert (segments[2].start, segments[2].end) == (2.0, 2.4)
        assert [len(s.words) for s in segments] == [2, 3, 1]

    def test_segment_words_are_independent_lists(self) -> None:
        words = [_Word("a.", 0.0, 0.1), _Word("b.", 0.2, 0.3)]
        segments = _group(words)
        segments[0].words.append(_Word("x", 9.0, 9.1))
        assert len(segments[1].words) == 1

    def test_no_words_no_text(self) -> None:
        assert _group([]) == []

    def test_no_words_with_text_yields_zero_length_segment(self) -> None:
        segments = _group([], full_text="untimed text")
        assert len(segments) == 1
        assert segments[0].text == "untimed text"
        assert (segments[0].start, segments[0].end) == (0.0, 0.0)
        assert segments[0].words == []

    def test_single_sentence_without_terminal_punctuation(self) -> None:
        words = [_Word("no", 0.0, 0.1), _Word("end", 0.2, 0.3)]
        segments = _group(words)
        assert len(segments) == 1
        assert segments[0].text == "no end"
