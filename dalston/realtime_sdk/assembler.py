"""Transcript assembly for real-time sessions.

Builds the full session transcript from individual utterances,
managing timestamps relative to session start time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    """Word with timing and confidence.

    Timestamps are relative to session start.
    """

    word: str
    start: float
    end: float
    confidence: float


@dataclass
class Segment:
    """Transcribed segment (utterance).

    Represents a complete utterance detected by VAD endpoint.
    Timestamps are relative to session start.
    """

    id: str
    start: float
    end: float
    text: str
    words: list[Word]
    confidence: float


@dataclass
class TranscribeResult:
    """Result from ASR transcription.

    Used as input to TranscriptAssembler.add_utterance().
    Word timestamps are relative to the audio segment (0-based).
    """

    text: str
    words: list[Word]
    language: str
    confidence: float


class TranscriptAssembler:
    """Assembles session transcript from individual utterances.

    Maintains the full transcript across multiple VAD-detected utterances,
    adjusting timestamps to session timeline.

    Example:
        assembler = TranscriptAssembler()

        # First utterance (0-2 seconds of speech)
        result = TranscribeResult(text="Hello", words=[...], ...)
        segment = assembler.add_utterance(result, audio_duration=2.0)
        # segment.start = 0.0, segment.end = 2.0

        # Second utterance (after silence, 1.5 seconds of speech)
        result = TranscribeResult(text="World", words=[...], ...)
        segment = assembler.add_utterance(result, audio_duration=1.5)
        # segment.start = 2.0, segment.end = 3.5

        # Full transcript
        print(assembler.get_full_transcript())  # "Hello World"
    """

    def __init__(self) -> None:
        """Initialize empty assembler."""
        self._segments: list[Segment] = []
        self._current_time: float = 0.0
        self._segment_counter: int = 0

    def add_utterance(
        self,
        result: TranscribeResult,
        audio_duration: float,
    ) -> Segment:
        """Add transcribed utterance to session transcript.

        Args:
            result: Transcription result from ASR engine
            audio_duration: Duration of the audio segment in seconds

        Returns:
            Segment with timestamps adjusted to session timeline
        """
        # Adjust word timestamps from segment-relative to session-relative
        adjusted_words = [
            Word(
                word=w.word,
                start=self._current_time + w.start,
                end=self._current_time + w.end,
                confidence=w.confidence,
            )
            for w in result.words
        ]

        # Create segment
        segment = Segment(
            id=f"seg_{self._segment_counter:04d}",
            start=self._current_time,
            end=self._current_time + audio_duration,
            text=result.text,
            words=adjusted_words,
            confidence=result.confidence,
        )

        self._segments.append(segment)
        self._segment_counter += 1
        self._current_time = segment.end

        return segment

    def get_full_transcript(self) -> str:
        """Get concatenated transcript text from all segments.

        Returns:
            Full transcript with segments joined by spaces
        """
        return " ".join(s.text for s in self._segments if s.text)

    def get_segments(self) -> list[Segment]:
        """Get all segments.

        Returns:
            List of all transcript segments in order
        """
        return list(self._segments)

    @property
    def current_time(self) -> float:
        """Current session timeline position in seconds.

        This is the end time of the last segment, or 0.0 if no segments yet.
        """
        return self._current_time

    @property
    def segment_count(self) -> int:
        """Number of segments in the transcript."""
        return len(self._segments)

    def reset(self) -> None:
        """Reset assembler for a new session.

        Clears all segments and resets timeline to 0.
        """
        self._segments.clear()
        self._current_time = 0.0
        self._segment_counter = 0
