"""Base class for batch transcription engines returning Transcript.

Provides ``_to_dalston_transcript()`` to eliminate per-engine mapping
boilerplate. Concrete engines implement ``transcribe_audio()`` and return
the canonical type directly.

Long-audio chunking (M86): subclasses may override
``get_max_audio_duration_s(task_request)`` to return a per-request audio
duration ceiling. When the input audio exceeds that value, ``process()``
dispatches to a chunked path that uses :class:`VadChunker` to split at
speech boundaries, calls ``transcribe_audio()`` per chunk, halves the
chunk cap and retries on CUDA OOM, and merges the resulting transcripts
with timestamps offset to the original timeline.
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

import dalston.metrics
import dalston.telemetry

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
from dalston.engine_sdk.inference.gpu_guard import clear_gpu_cache, is_oom_error
from dalston.engine_sdk.types import TaskRequest, TaskResponse
from dalston.engine_sdk.vad import VadChunker

logger = structlog.get_logger()


_MIN_CHUNK_FLOOR_S = 60.0


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

    def get_max_audio_duration_s(
        self,
        task_request: TaskRequest,
    ) -> float | None:
        """Return the per-request audio duration limit, or None.

        Subclasses override this to opt into base-engine VAD chunking.
        The default returns ``None``, meaning no chunking — ``process()``
        calls ``transcribe_audio()`` directly with the full input.

        When this returns a non-None value and the input audio exceeds
        it, ``process()`` dispatches to :meth:`_process_chunked`, which
        splits the audio at speech boundaries via :class:`VadChunker`,
        runs ``transcribe_audio()`` per chunk with OOM backoff, and
        merges the results.

        Args:
            task_request: The incoming task request.

        Returns:
            Duration cap in seconds, or ``None`` for no chunking.
        """
        return None

    def process(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Process a task, chunking long audio when the engine opts in.

        Subclasses should not override this. Override
        ``transcribe_audio`` for per-chunk inference logic and
        ``get_max_audio_duration_s`` to declare the chunking limit.
        """
        max_s = self.get_max_audio_duration_s(task_request)
        if max_s is None or task_request.audio_path is None:
            transcript = self.transcribe_audio(task_request, ctx)
            return TaskResponse(data=transcript)

        audio_duration = self._audio_duration_s(task_request.audio_path)
        if audio_duration is None or audio_duration <= max_s:
            transcript = self.transcribe_audio(task_request, ctx)
            return TaskResponse(data=transcript)

        return self._process_chunked(task_request, ctx, max_s, audio_duration)

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
    # Chunked path (M86)
    # ------------------------------------------------------------------

    def _process_chunked(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
        max_chunk_s: float,
        audio_duration_s: float,
    ) -> TaskResponse:
        """Split long audio, transcribe each chunk, merge the results.

        Wraps the whole run in a single top-level ``engine.recognize``
        span so chunked requests report as one inference in observability
        (M86.6). Halves ``max_chunk_s`` on CUDA OOM and retries the
        remaining chunks (M86.5).
        """
        start_wall = time.monotonic()
        engine_id = getattr(self, "engine_id", "unknown")
        effective_max_s = self._effective_chunk_cap(max_chunk_s)

        with dalston.telemetry.create_span(
            "engine.recognize",
            attributes={
                "dalston.chunked": True,
                "dalston.chunk_max_s": effective_max_s,
                "dalston.audio_duration_s": round(audio_duration_s, 3),
            },
        ):
            with tempfile.TemporaryDirectory(prefix="dalston_chunks_") as tmp_str:
                tmp_dir = Path(tmp_str)
                merged = self._transcribe_chunks_with_backoff(
                    task_request,
                    ctx,
                    effective_max_s,
                    tmp_dir,
                )

            wall_time = time.monotonic() - start_wall
            dalston.telemetry.set_span_attribute(
                "dalston.chunk_count", merged["chunk_count"]
            )
            dalston.telemetry.set_span_attribute(
                "dalston.chunk_max_s_final", merged["final_max_s"]
            )

            dalston.metrics.observe_engine_recognize(
                engine_id, "", "cpu-or-gpu", wall_time
            )
            if audio_duration_s > 0:
                rtf = wall_time / audio_duration_s
                dalston.metrics.observe_engine_realtime_factor(
                    engine_id, "", "cpu-or-gpu", rtf
                )
                dalston.telemetry.set_span_attribute("dalston.rtf", round(rtf, 4))

            return TaskResponse(data=merged["transcript"])

    def _transcribe_chunks_with_backoff(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
        max_chunk_s: float,
        tmp_dir: Path,
    ) -> dict[str, Any]:
        """Run VAD chunking + per-chunk transcribe + OOM backoff.

        Every pass splits the *original* source file from scratch. The
        ``remaining_start_s`` boundary marks the earliest absolute
        offset that still needs work — chunks whose offset lies before
        it were already completed by an earlier pass and are filtered
        out, so timestamps remain absolute in the source's timeline and
        no audio is reprocessed after an OOM retry.

        Returns a dict with ``transcript`` (merged Transcript),
        ``chunk_count`` (total chunks processed), and ``final_max_s``
        (the effective chunk cap after any OOM halving).
        """
        assert task_request.audio_path is not None
        source_audio_path = task_request.audio_path

        completed_transcripts: list[tuple[Transcript, float]] = []
        current_max_s = max_chunk_s
        remaining_start_s = 0.0
        total_chunks = 0
        retry_pass = 0

        while True:
            retry_pass += 1
            chunker = VadChunker(max_chunk_duration_s=current_max_s)
            sub_tmp = tmp_dir / f"pass_{retry_pass}_max_{int(current_max_s)}"
            sub_tmp.mkdir(parents=True, exist_ok=True)
            try:
                all_chunks = chunker.split(source_audio_path, sub_tmp)
            except Exception:
                logger.exception(
                    "vad_split_failed",
                    audio_path=str(source_audio_path),
                    max_chunk_s=current_max_s,
                )
                raise

            chunks = [c for c in all_chunks if c.offset + 1e-3 >= remaining_start_s]

            if not chunks:
                break

            oom_index: int | None = None
            processed_in_pass = 0
            for idx, chunk in enumerate(chunks):
                chunk_request = task_request.replace(audio_path=chunk.audio_path)
                try:
                    with dalston.telemetry.create_span(
                        "engine.chunk_recognize",
                        attributes={
                            "dalston.chunk_index": total_chunks + idx,
                            "dalston.chunk_duration_s": round(chunk.duration, 3),
                            "dalston.chunk_offset_s": round(chunk.offset, 3),
                        },
                    ):
                        transcript = self.transcribe_audio(chunk_request, ctx)
                except Exception as exc:
                    if not is_oom_error(exc):
                        raise
                    oom_index = idx
                    clear_gpu_cache()
                    break
                completed_transcripts.append((transcript, chunk.offset))
                processed_in_pass += 1

            total_chunks += processed_in_pass

            if oom_index is None:
                break

            new_max_s = max(current_max_s / 2.0, _MIN_CHUNK_FLOOR_S)
            if new_max_s >= current_max_s:
                logger.error(
                    "chunked_oom_floor_reached",
                    floor_s=_MIN_CHUNK_FLOOR_S,
                    chunk_index=total_chunks,
                )
                raise RuntimeError(
                    "CUDA OOM at chunk floor "
                    f"{_MIN_CHUNK_FLOOR_S}s — cannot reduce chunk size further"
                )
            logger.warning(
                "chunked_oom_backoff",
                old_max_s=current_max_s,
                new_max_s=new_max_s,
                remaining_start_s=round(chunks[oom_index].offset, 3),
                chunks_completed=total_chunks,
            )
            self._cache_chunk_cap(new_max_s)
            current_max_s = new_max_s
            remaining_start_s = chunks[oom_index].offset

        merged = self._merge_chunk_transcripts(completed_transcripts)
        return {
            "transcript": merged,
            "chunk_count": total_chunks,
            "final_max_s": current_max_s,
        }

    def _merge_chunk_transcripts(
        self,
        chunk_results: list[tuple[Transcript, float]],
    ) -> Transcript:
        """Merge N chunk transcripts into one canonical Transcript.

        Offsets segment and word timestamps by the chunk offset, then
        concatenates text with a space separator. See M86 §86.2 for the
        full merge contract.
        """
        if not chunk_results:
            return self.build_transcript(
                text="",
                segments=[],
                language="en",
                engine_id=getattr(self, "engine_id", "unknown"),
            )

        first_transcript = chunk_results[0][0]
        language = first_transcript.language
        alignment_method = first_transcript.alignment_method
        engine_id = first_transcript.engine_id
        channel = first_transcript.channel

        language_confidences = [
            float(t.language_confidence)
            for t, _ in chunk_results
            if t.language_confidence is not None
        ]
        if language_confidences:
            language_confidence: float | None = sum(language_confidences) / len(
                language_confidences
            )
        else:
            language_confidence = None

        all_segments: list[TranscriptSegment] = []
        all_text: list[str] = []
        all_warnings: list[str] = []
        seen_warnings: set[str] = set()

        for transcript, offset in chunk_results:
            if transcript.text:
                all_text.append(transcript.text.strip())
            for warn in transcript.warnings or []:
                if warn not in seen_warnings:
                    seen_warnings.add(warn)
                    all_warnings.append(warn)

            for seg in transcript.segments:
                shifted_words: list[TranscriptWord] | None = None
                if seg.words is not None:
                    shifted_words = [
                        self.build_word(
                            text=w.text,
                            start=round(w.start + offset, 3),
                            end=round(w.end + offset, 3),
                            confidence=w.confidence,
                            alignment_method=w.alignment_method,
                            characters=w.characters,
                            phonemes=w.phonemes,
                            **(w.metadata or {}),
                        )
                        for w in seg.words
                    ]

                all_segments.append(
                    self.build_segment(
                        start=round(seg.start + offset, 3),
                        end=round(seg.end + offset, 3),
                        text=seg.text,
                        words=shifted_words,
                        language=seg.language,
                        confidence=seg.confidence,
                        segment_id=seg.id,
                        is_final=seg.is_final,
                        is_speech=seg.is_speech,
                        **(seg.metadata or {}),
                    )
                )

        return self.build_transcript(
            text=" ".join(all_text).strip(),
            segments=all_segments,
            language=language,
            engine_id=engine_id,
            language_confidence=language_confidence,
            alignment_method=alignment_method,
            channel=channel,
            warnings=all_warnings or None,
        )

    # ------------------------------------------------------------------
    # Chunk cap caching + audio duration probing
    # ------------------------------------------------------------------

    def _effective_chunk_cap(self, requested_max_s: float) -> float:
        """Return the chunk cap to use, clamping to any OOM-cached floor."""
        cached = getattr(self, "_chunked_oom_cap_s", None)
        if cached is not None and cached < requested_max_s:
            return float(cached)
        return requested_max_s

    def _cache_chunk_cap(self, new_max_s: float) -> None:
        """Cache a safe chunk cap after OOM so subsequent tasks skip it."""
        self._chunked_oom_cap_s = new_max_s

    @staticmethod
    def _audio_duration_s(audio_path: Path) -> float | None:
        """Probe audio duration in seconds without decoding the full file.

        Uses ``soundfile.info()`` (fast, no decode). Returns None if the
        file can't be inspected — the caller falls back to the
        non-chunked path.
        """
        with contextlib.suppress(Exception):
            import soundfile as sf

            info = sf.info(str(audio_path))
            if info.samplerate > 0:
                return float(info.frames) / float(info.samplerate)
        return None

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
                vram_params = adaptive.select(queue_depth)
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
