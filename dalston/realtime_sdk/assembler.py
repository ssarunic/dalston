"""Transcript assembly for real-time sessions.

Builds the full session transcript from individual utterances,
managing timestamps relative to session start time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dalston.common.pipeline_types import Transcript


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


class TranscriptAssembler:
    """Assembles session transcript from individual utterances.

    Maintains the full transcript across multiple VAD-detected utterances,
    adjusting timestamps to session timeline.

    Example:
        from dalston.common.pipeline_types import Transcript, TranscriptSegment

        assembler = TranscriptAssembler()

        # First utterance (0-2 seconds of speech)
        transcript = Transcript(text="Hello", segments=[...], ...)
        segment = assembler.add_transcript(transcript, audio_duration=2.0)
        # segment.start = 0.0, segment.end = 2.0

        # Full transcript
        print(assembler.get_full_transcript())  # "Hello"
    """

    def __init__(self) -> None:
        """Initialize empty assembler."""
        self._segments: list[Segment] = []
        self._current_time: float = 0.0
        self._segment_counter: int = 0

    def add_transcript(
        self,
        transcript: Transcript,
        audio_duration: float,
    ) -> Segment:
        """Add a Transcript result to the session transcript.

        Extracts words from all transcript segments and adjusts timestamps
        to the session timeline.

        Args:
            transcript: Unified transcript from ASR engine
            audio_duration: Duration of the audio segment in seconds

        Returns:
            Segment with timestamps adjusted to session timeline
        """
        # Collect all words from transcript segments
        adjusted_words: list[Word] = []
        for seg in transcript.segments:
            if seg.words:
                for w in seg.words:
                    adjusted_words.append(
                        Word(
                            word=w.text,
                            start=self._current_time + w.start,
                            end=self._current_time + w.end,
                            confidence=w.confidence
                            if w.confidence is not None
                            else 0.0,
                        )
                    )

        # Compute overall confidence from segment confidences, falling back
        # to transcript-level language_confidence when segments omit it.
        seg_confidences = [
            s.confidence for s in transcript.segments if s.confidence is not None
        ]
        if seg_confidences:
            overall_confidence = sum(seg_confidences) / len(seg_confidences)
        elif transcript.language_confidence is not None:
            overall_confidence = transcript.language_confidence
        else:
            overall_confidence = 0.0

        segment = Segment(
            id=f"seg_{self._segment_counter:04d}",
            start=self._current_time,
            end=self._current_time + audio_duration,
            text=transcript.text,
            words=adjusted_words,
            confidence=overall_confidence,
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
