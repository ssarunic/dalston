"""Base class for batch transcription engines returning Transcript.

Provides ``_to_dalston_transcript()`` to eliminate per-engine mapping
boilerplate. Concrete engines implement ``transcribe_audio()`` and return
the canonical type directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dalston.engine_sdk.http_server import EngineHTTPServer

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Character,
    Phoneme,
    TimestampGranularity,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import TaskRequest, TaskResponse


class BaseBatchTranscribeEngine(Engine):
    """Base class for batch transcription engines.

    Subclasses implement ``transcribe_audio()`` which returns a
    ``Transcript``. The ``process()`` method wraps it in an
    ``TaskResponse`` envelope.

    Helper methods are provided for building the canonical types from
    common data shapes.
    """

    def create_http_server(self, port: int = 9100) -> EngineHTTPServer:
        """Return a ``TranscribeHTTPServer`` with ``POST /v1/transcribe``."""
        from dalston.engine_sdk.http_transcribe import TranscribeHTTPServer

        return TranscribeHTTPServer(engine=self, port=port)

    def process(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Process a task by delegating to ``transcribe_audio``.

        Subclasses should not override this. Override ``transcribe_audio``
        instead.
        """
        transcript = self.transcribe_audio(task_request, ctx)
        return TaskResponse(data=transcript)

    def transcribe_audio(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> Transcript:
        """Transcribe audio and return a Transcript.

        Must be implemented by subclasses.

        Args:
            task_request: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            Canonical transcript output
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Adaptive parameter helpers
    # ------------------------------------------------------------------

    def _resolve_adaptive_batch_size(self, fallback: int | None = 1) -> int | None:
        """Resolve vad_batch_size from adaptive VRAM budget.

        Priority: runner adaptive params (queue-depth aware) > fallback.
        For explicit per-request overrides (e.g. from HTTP calibration),
        callers should check ``params.vad_batch_size`` first before
        calling this method.

        Args:
            fallback: Value when no adaptive params are available.

        Returns:
            Batch size from VRAM budget, or *fallback*.
        """
        runner = getattr(self, "_runner", None)
        if runner is not None:
            adaptive = runner.get_adaptive_params()
            if adaptive is not None:
                queue_depth = runner.get_queue_depth()
                vram_params = adaptive.select(queue_depth, inflight=1)
                return vram_params.vad_batch_size
        return fallback

    # ------------------------------------------------------------------
    # Helper builders
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
        """Build a ``TranscriptSegment`` with optional metadata.

        Model-specific fields (compression_ratio, no_speech_prob, avg_logprob,
        tokens, temperature, etc.) go into ``extra`` and are stored in
        ``metadata``.
        """
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
        channel: int | None = None,
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
            channel=channel,
            warnings=warnings or [],
            metadata=extra if extra else {},
        )
