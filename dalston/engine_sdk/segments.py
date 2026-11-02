"""Group word-level results into sentence segments.

Some inference backends (ONNX Parakeet, FluidAudio) return words with
timings but no segment structure, while downstream stages (align, PII
detection, merge) read ``transcript.segments[].words``.  This module holds
the one grouping rule both engines use: a segment closes on a word that
ends with sentence-terminating punctuation.

The helper is deliberately generic over the word and segment types so
engines can keep their own dataclasses or Pydantic models.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

#: Characters that end a sentence. Includes the full-width CJK forms so
#: Chinese and Japanese output splits as well as Latin-script output.
SENTENCE_ENDINGS: tuple[str, ...] = (".", "?", "!", "。", "？", "！")

W = TypeVar("W")
S = TypeVar("S")


def is_sentence_ending(text: str) -> bool:
    """Return True if ``text`` ends with sentence-terminating punctuation."""
    return text.rstrip().endswith(SENTENCE_ENDINGS)


def words_to_segments(
    words: Sequence[W],
    *,
    word_text: Callable[[W], str],
    word_start: Callable[[W], float],
    word_end: Callable[[W], float],
    make_segment: Callable[[float, float, str, list[W]], S],
    full_text: str = "",
) -> list[S]:
    """Group ``words`` into segments, closing one on each sentence-ending word.

    Args:
        words: Word results in time order.
        word_text: Accessor for a word's text.
        word_start: Accessor for a word's start time in seconds.
        word_end: Accessor for a word's end time in seconds.
        make_segment: Factory called as ``(start, end, text, words)`` for each
            segment.  Segment text is the words joined by single spaces.
        full_text: Used only when ``words`` is empty: a non-empty value yields
            a single zero-length segment carrying the text, so a transcript
            without timings still has somewhere to put its text.

    Returns:
        Segments in time order.  Empty when there are no words and no text.
    """
    if not words:
        if full_text:
            return [make_segment(0.0, 0.0, full_text, [])]
        return []

    segments: list[S] = []
    current: list[W] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(word_text(w) for w in current)
        segments.append(
            make_segment(
                word_start(current[0]), word_end(current[-1]), text, list(current)
            )
        )
        current.clear()

    for word in words:
        current.append(word)
        if is_sentence_ending(word_text(word)):
            flush()
    flush()

    return segments
