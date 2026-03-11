"""Base class for real-time transcription engines returning DalstonTranscriptV1.

Provides a bridge between the new unified transcript contract and the
existing ``SessionHandler`` which expects ``TranscribeResult``. Concrete
engines implement ``transcribe_v1()`` and return ``DalstonTranscriptV1``;
the base class auto-converts to ``TranscribeResult`` for the session layer.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Character,
    DalstonTranscriptV1,
    Phoneme,
    TimestampGranularity,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.realtime_sdk.assembler import TranscribeResult, Word
from dalston.realtime_sdk.base import RealtimeEngine


class BaseRealtimeTranscribeEngine(RealtimeEngine):
    """Base class for real-time engines that produce DalstonTranscriptV1.

    Subclasses implement ``transcribe_v1()`` which returns the canonical
    transcript type. The ``transcribe()`` method auto-converts to the
    ``TranscribeResult`` that ``SessionHandler`` expects.

    This allows engines to be migrated one at a time without changing the
    session handling layer.
    """

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Convert DalstonTranscriptV1 to TranscribeResult for SessionHandler.

        Subclasses should override ``transcribe_v1()`` instead.
        """
        transcript = self.transcribe_v1(audio, language, model_variant, vocabulary)
        return self._to_transcribe_result(transcript)

    def transcribe_v1(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> DalstonTranscriptV1:
        """Transcribe audio and return a DalstonTranscriptV1.

        Must be implemented by subclasses.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (e.g., "en") or "auto" for detection
            model_variant: Model name (e.g., "large-v3-turbo")
            vocabulary: List of terms to boost recognition

        Returns:
            Canonical transcript output
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_transcribe_result(transcript: DalstonTranscriptV1) -> TranscribeResult:
        """Convert a DalstonTranscriptV1 to a TranscribeResult."""
        words: list[Word] = []
        for seg in transcript.segments:
            if seg.words:
                for w in seg.words:
                    words.append(
                        Word(
                            word=w.text,
                            start=w.start,
                            end=w.end,
                            confidence=w.confidence if w.confidence is not None else 0.0,
                        )
                    )

        confidence = transcript.language_confidence if transcript.language_confidence is not None else 0.0

        return TranscribeResult(
            text=transcript.text,
            words=words,
            language=transcript.language,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Builder helpers (same API as BaseBatchTranscribeEngine)
    # ------------------------------------------------------------------

    @staticmethod
    def build_word(
        text: str,
        start: float,
        end: float,
        confidence: float | None = None,
        alignment_method: AlignmentMethod = AlignmentMethod.UNKNOWN,
        characters: list[Character] | None = None,
        phonemes: list[Phoneme] | None = None,
        **extra: Any,
    ) -> TranscriptWord:
        """Build a ``TranscriptWord`` with optional metadata."""
        return TranscriptWord(
            text=text,
            start=start,
            end=end,
            confidence=confidence,
            alignment_method=alignment_method,
            characters=characters,
            phonemes=phonemes,
            metadata=extra if extra else {},
        )

    @staticmethod
    def build_segment(
        start: float,
        end: float,
        text: str,
        words: list[TranscriptWord] | None = None,
        language: str | None = None,
        confidence: float | None = None,
        segment_id: str | None = None,
        is_final: bool | None = None,
        is_speech: bool | None = None,
        **extra: Any,
    ) -> TranscriptSegment:
        """Build a ``TranscriptSegment`` with optional metadata."""
        return TranscriptSegment(
            id=segment_id,
            start=start,
            end=end,
            text=text,
            words=words,
            language=language,
            confidence=confidence,
            is_final=is_final,
            is_speech=is_speech,
            metadata=extra if extra else {},
        )

    @staticmethod
    def build_transcript(
        text: str,
        segments: list[TranscriptSegment],
        language: str,
        runtime: str,
        language_confidence: float | None = None,
        duration: float | None = None,
        alignment_method: AlignmentMethod = AlignmentMethod.UNKNOWN,
        warnings: list[str] | None = None,
        **extra: Any,
    ) -> DalstonTranscriptV1:
        """Build a ``DalstonTranscriptV1`` from assembled parts."""
        has_words = any(seg.words for seg in segments if seg.words is not None)
        granularity = (
            TimestampGranularity.WORD if has_words else TimestampGranularity.SEGMENT
        )

        return DalstonTranscriptV1(
            text=text,
            segments=segments,
            language=language,
            language_confidence=language_confidence,
            duration=duration,
            timestamp_granularity=granularity,
            alignment_method=alignment_method,
            runtime=runtime,
            warnings=warnings or [],
            metadata=extra if extra else {},
        )
