"""Base class for real-time transcription engines returning Transcript.

Concrete engines implement ``transcribe_v1()`` and return ``Transcript``.
The ``transcribe()`` method delegates directly to ``transcribe_v1()``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Character,
    Phoneme,
    TimestampGranularity,
    TranscribeInput,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.realtime_sdk.base import RealtimeEngine


class BaseRealtimeTranscribeEngine(RealtimeEngine):
    """Base class for real-time engines that produce Transcript.

    Subclasses implement ``transcribe_v1()`` which returns the canonical
    transcript type. The ``transcribe()`` method delegates to it.
    """

    def transcribe(
        self,
        audio: np.ndarray,
        params: TranscribeInput,
    ) -> Transcript:
        """Transcribe audio and return a Transcript.

        Subclasses should override ``transcribe_v1()`` instead.
        """
        return self.transcribe_v1(audio, params)

    def transcribe_v1(
        self,
        audio: np.ndarray,
        params: TranscribeInput,
    ) -> Transcript:
        """Transcribe audio and return a Transcript.

        Must be implemented by subclasses.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            params: Typed transcriber parameters for this utterance

        Returns:
            Canonical transcript output
        """
        raise NotImplementedError

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
        engine_id: str,
        language_confidence: float | None = None,
        duration: float | None = None,
        alignment_method: AlignmentMethod = AlignmentMethod.UNKNOWN,
        warnings: list[str] | None = None,
        **extra: Any,
    ) -> Transcript:
        """Build a ``Transcript`` from assembled parts."""
        has_words = any(seg.words for seg in segments if seg.words is not None)
        granularity = (
            TimestampGranularity.WORD if has_words else TimestampGranularity.SEGMENT
        )

        return Transcript(
            text=text,
            segments=segments,
            language=language,
            language_confidence=language_confidence,
            duration=duration,
            timestamp_granularity=granularity,
            alignment_method=alignment_method,
            engine_id=engine_id,
            warnings=warnings or [],
            metadata=extra if extra else {},
        )
