"""Shared inference core for faster-whisper batch and realtime engines.

Extracts common model loading and transcription logic so that both the
batch engine (queue-based) and the realtime engine (WebSocket-based) can
share a single loaded model and inference path.

This module owns the FasterWhisperModelManager and provides a
engine_id-neutral interface for transcription. Each engine adapter
(batch / realtime) is responsible for formatting the raw results into
its own output contract.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog

import dalston.metrics
import dalston.telemetry
from dalston.engine_sdk.managers import FasterWhisperModelManager

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result types — engine_id-neutral, no dependency on batch or RT SDK types
# ---------------------------------------------------------------------------


@dataclass
class WordResult:
    """A single word with timing and confidence from faster-whisper."""

    word: str
    start: float
    end: float
    probability: float


@dataclass
class SegmentResult:
    """A transcription segment from faster-whisper."""

    start: float
    end: float
    text: str
    words: list[WordResult]
    tokens: list[int] | None = None
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    no_speech_prob: float | None = None


@dataclass
class TranscriptionResult:
    """Complete transcription result from a single inference call."""

    segments: list[SegmentResult] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 0.0
    duration: float = 0.0


# ---------------------------------------------------------------------------
# Transcription configuration
# ---------------------------------------------------------------------------


@dataclass
class FasterWhisperConfig:
    """Parameters for a single transcription call.

    Both batch and RT adapters construct this from their own config
    sources (task config dict, query params, etc.).
    """

    language: str | None = None
    beam_size: int = 5
    vad_batch_size: int = 1
    vad_filter: bool = True
    word_timestamps: bool = True
    temperature: float | list[float] = 0.0
    task: str = "transcribe"
    initial_prompt: str | None = None
    hotwords: str | None = None
    # Anti-hallucination defaults aligned with WhisperX
    condition_on_previous_text: bool = False
    no_speech_threshold: float = 0.6
    compression_ratio_threshold: float = 2.4
    hallucination_silence_threshold: float | None = 2.0
    repetition_penalty: float = 1.0


# ---------------------------------------------------------------------------
# FasterWhisperInference — shared inference logic
# ---------------------------------------------------------------------------


class FasterWhisperInference:
    """Shared inference logic for faster-whisper batch and realtime.

    Owns the model manager and provides a unified transcription interface.
    Both batch and realtime adapters delegate inference here while keeping
    their own I/O contracts and output formatting.
    """

    # Valid model name mappings for normalization
    MODEL_ALIASES: dict[str, str] = {
        "faster-whisper-large-v3": "large-v3",
        "faster-whisper-large-v3-turbo": "large-v3-turbo",
        "faster-whisper-distil-large-v3": "distil-large-v3",
        "whisper-large-v3": "large-v3",
        "whisper-large-v3-turbo": "large-v3-turbo",
    }

    SUPPORTED_MODELS: list[str] = [
        "large-v3-turbo",
        "large-v3",
        "distil-large-v3",
        "large-v2",
        "medium",
        "small",
        "base",
        "tiny",
    ]

    def __init__(
        self,
        device: str | None = None,
        compute_type: str | None = None,
        model_storage: object | None = None,
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
    ) -> None:
        from dalston.engine_sdk.device import detect_device

        if device is not None and compute_type is not None:
            self._device = device
            self._compute_type = compute_type
        else:
            self._device = device or detect_device(include_mps=False)
            self._compute_type = compute_type or (
                "float16" if self._device == "cuda" else "int8"
            )

        self._manager = FasterWhisperModelManager(
            device=self._device,
            compute_type=self._compute_type,
            model_storage=model_storage,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
            preload=preload,
        )
        self._current_model_id: str | None = None

        logger.info(
            "transcribe_core_init",
            device=self._device,
            compute_type=self._compute_type,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
        )

    # -- Properties ----------------------------------------------------------

    @property
    def device(self) -> str:
        return self._device

    @property
    def compute_type(self) -> str:
        return self._compute_type

    @property
    def manager(self) -> FasterWhisperModelManager:
        """Expose manager for stats, shutdown, and cache queries."""
        return self._manager

    # -- Model normalization -------------------------------------------------

    def normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to faster-whisper supported format."""
        return self.MODEL_ALIASES.get(model_id, model_id)

    # -- Core transcription --------------------------------------------------

    def transcribe(
        self,
        audio: str | Path | np.ndarray,
        model_id: str,
        config: FasterWhisperConfig | None = None,
    ) -> TranscriptionResult:
        """Run transcription on audio input.

        Works with both file paths (batch) and numpy arrays (realtime).

        Args:
            audio: File path string/Path or numpy float32 array (mono, 16kHz)
            model_id: Model identifier (e.g. "large-v3-turbo")
            config: Transcription parameters. Defaults to sensible values.

        Returns:
            TranscriptionResult with segments, language, and duration.
        """
        if config is None:
            config = FasterWhisperConfig()

        model_id = self.normalize_model_id(model_id)
        self._current_model_id = model_id
        model = self._manager.acquire(model_id)
        try:
            return self._transcribe_with_model(model, audio, config)
        finally:
            self._manager.release(model_id)
            self._current_model_id = None

    def _transcribe_with_model(
        self,
        model: WhisperModel,
        audio: str | Path | np.ndarray,
        config: FasterWhisperConfig,
    ) -> TranscriptionResult:
        """Execute transcription with an already-acquired model.

        When ``vad_batch_size > 1``, runs Silero VAD to segment the audio
        into speech chunks, groups them into batches of up to
        ``vad_batch_size`` chunks, and transcribes each batch sequentially.
        This mirrors the whisperX batching strategy and gives a directly
        measurable VRAM knob comparable across engines (ONNX, NeMo, etc.).

        When ``vad_batch_size == 1`` (default / realtime), falls through to
        a single ``model.transcribe()`` call with no extra overhead.
        """
        # Normalize audio input
        audio_input: str | np.ndarray
        if isinstance(audio, Path):
            audio_input = str(audio)
        else:
            audio_input = audio

        # Handle language
        language = config.language
        if language == "auto" or language == "":
            language = None

        # Build kwargs for faster-whisper (no batch_size — WhisperModel
        # doesn't accept it; batching is handled via VAD chunking below)
        transcribe_kwargs: dict = {
            "language": language,
            "beam_size": config.beam_size,
            "vad_filter": config.vad_filter,
            "word_timestamps": config.word_timestamps,
            "temperature": config.temperature,
            "condition_on_previous_text": config.condition_on_previous_text,
            "no_speech_threshold": config.no_speech_threshold,
            "compression_ratio_threshold": config.compression_ratio_threshold,
            "repetition_penalty": config.repetition_penalty,
        }

        if config.hallucination_silence_threshold is not None:
            transcribe_kwargs["hallucination_silence_threshold"] = (
                config.hallucination_silence_threshold
            )

        if config.task in {"transcribe", "translate"}:
            transcribe_kwargs["task"] = config.task

        if config.initial_prompt:
            transcribe_kwargs["initial_prompt"] = config.initial_prompt

        if config.hotwords:
            transcribe_kwargs["hotwords"] = config.hotwords

        engine_id = os.environ.get("DALSTON_ENGINE_ID", "faster-whisper")
        model_id = self._current_model_id or ""

        # Run inference — span covers transcribe() + segment iteration
        start = time.monotonic()
        with dalston.telemetry.create_span(
            "engine.recognize",
            attributes={
                "dalston.device": self._device,
                "dalston.compute_type": self._compute_type,
                "dalston.beam_size": config.beam_size,
                "dalston.vad_batch_size": config.vad_batch_size,
                "dalston.vad_filter": config.vad_filter,
            },
        ):
            if config.vad_batch_size > 1 and isinstance(audio_input, str):
                segments, info = self._transcribe_vad_batched(
                    model,
                    audio_input,
                    config.vad_batch_size,
                    transcribe_kwargs,
                )
            else:
                segments, info = self._transcribe_single(
                    model,
                    audio_input,
                    transcribe_kwargs,
                )

        recognize_time = time.monotonic() - start
        audio_duration_s = info.duration

        dalston.metrics.observe_engine_recognize(
            engine_id, model_id, self._device, recognize_time
        )
        if audio_duration_s > 0:
            rtf = recognize_time / audio_duration_s
            dalston.metrics.observe_engine_realtime_factor(
                engine_id, model_id, self._device, rtf
            )
            dalston.telemetry.set_span_attribute("dalston.rtf", round(rtf, 4))
            dalston.telemetry.set_span_attribute(
                "dalston.audio_duration_s", round(audio_duration_s, 3)
            )

        word_count = sum(len(s.words) for s in segments)
        dalston.telemetry.set_span_attribute("dalston.word_count", word_count)
        dalston.telemetry.set_span_attribute("dalston.segment_count", len(segments))

        return TranscriptionResult(
            segments=segments,
            language=info.language,
            language_probability=info.language_probability,
            duration=info.duration,
        )

    # -- Internal transcription helpers --------------------------------------

    @staticmethod
    def _collect_segments(segments_generator) -> list[SegmentResult]:  # noqa: ANN001
        """Iterate faster-whisper segment generator into neutral result types."""
        segments: list[SegmentResult] = []
        for segment in segments_generator:
            words: list[WordResult] = []
            if segment.words:
                words = [
                    WordResult(
                        word=w.word.strip(),
                        start=round(w.start, 3),
                        end=round(w.end, 3),
                        probability=round(w.probability, 3),
                    )
                    for w in segment.words
                ]

            raw_tokens = getattr(segment, "tokens", None)
            raw_avg_logprob = getattr(segment, "avg_logprob", None)
            raw_compression_ratio = getattr(segment, "compression_ratio", None)
            raw_no_speech_prob = getattr(segment, "no_speech_prob", None)

            segments.append(
                SegmentResult(
                    start=round(segment.start, 3),
                    end=round(segment.end, 3),
                    text=segment.text.strip(),
                    words=words,
                    tokens=list(raw_tokens) if raw_tokens else None,
                    avg_logprob=(
                        round(raw_avg_logprob, 4)
                        if raw_avg_logprob is not None
                        else None
                    ),
                    compression_ratio=(
                        round(raw_compression_ratio, 4)
                        if raw_compression_ratio is not None
                        else None
                    ),
                    no_speech_prob=(
                        round(raw_no_speech_prob, 4)
                        if raw_no_speech_prob is not None
                        else None
                    ),
                )
            )
        return segments

    def _transcribe_single(
        self,
        model: WhisperModel,
        audio_input: str | np.ndarray,
        transcribe_kwargs: dict,
    ) -> tuple[list[SegmentResult], object]:
        """Standard single-pass transcription (vad_batch_size=1)."""
        segments_generator, info = model.transcribe(
            audio_input,
            **transcribe_kwargs,
        )
        return self._collect_segments(segments_generator), info

    def _transcribe_vad_batched(
        self,
        model: WhisperModel,
        audio_path: str,
        vad_batch_size: int,
        transcribe_kwargs: dict,
    ) -> tuple[list[SegmentResult], object]:
        """VAD-chunked batched transcription (whisperX-style).

        1. Run Silero VAD to find speech segments
        2. Group VAD segments into batches of ``vad_batch_size``
        3. Transcribe each batch (chunk) through the model sequentially

        Each chunk is sliced from the full audio and transcribed independently.
        Segment timestamps are offset back to the original audio timeline.
        """
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        SAMPLE_RATE = 16000

        # Load and run VAD
        full_audio = decode_audio(audio_path, sampling_rate=SAMPLE_RATE)
        vad_opts = VadOptions()
        speech_timestamps = get_speech_timestamps(full_audio, vad_opts)

        if not speech_timestamps:
            # No speech detected — run single pass to get info (language, duration)
            return self._transcribe_single(model, audio_path, transcribe_kwargs)

        # Group VAD segments into batches
        all_segments: list[SegmentResult] = []
        info = None

        # Disable model-level VAD since we already ran it
        batch_kwargs = {**transcribe_kwargs, "vad_filter": False}

        for batch_start in range(0, len(speech_timestamps), vad_batch_size):
            batch = speech_timestamps[batch_start : batch_start + vad_batch_size]

            # Determine audio slice covering this batch
            chunk_start_sample = batch[0]["start"]
            chunk_end_sample = batch[-1]["end"]
            chunk_audio = full_audio[chunk_start_sample:chunk_end_sample]
            chunk_offset_s = chunk_start_sample / SAMPLE_RATE

            segments_generator, chunk_info = model.transcribe(
                chunk_audio,
                **batch_kwargs,
            )

            if info is None:
                info = chunk_info

            # Collect and offset timestamps back to original timeline
            for seg in self._collect_segments(segments_generator):
                all_segments.append(
                    SegmentResult(
                        start=round(seg.start + chunk_offset_s, 3),
                        end=round(seg.end + chunk_offset_s, 3),
                        text=seg.text,
                        words=[
                            WordResult(
                                word=w.word,
                                start=round(w.start + chunk_offset_s, 3),
                                end=round(w.end + chunk_offset_s, 3),
                                probability=w.probability,
                            )
                            for w in seg.words
                        ],
                        tokens=seg.tokens,
                        avg_logprob=seg.avg_logprob,
                        compression_ratio=seg.compression_ratio,
                        no_speech_prob=seg.no_speech_prob,
                    )
                )

            logger.debug(
                "vad_batch_transcribed",
                batch_idx=batch_start // vad_batch_size,
                vad_segments=len(batch),
                result_segments=len(all_segments),
                chunk_offset_s=round(chunk_offset_s, 3),
            )

        # Patch info.duration to reflect full audio
        info.duration = len(full_audio) / SAMPLE_RATE

        return all_segments, info

    # -- Lifecycle -----------------------------------------------------------

    def get_stats(self) -> dict:
        """Get model manager statistics."""
        return self._manager.get_stats()

    def get_local_cache_stats(self) -> dict | None:
        """Get local model cache statistics from S3ModelStorage."""
        return self._manager.get_local_cache_stats()

    def shutdown(self) -> None:
        """Shutdown core and release all models."""
        logger.info("transcribe_core_shutdown")
        self._manager.shutdown()

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> FasterWhisperInference:
        """Create a FasterWhisperInference configured from environment variables.

        Args:
            device: Override device (None = auto-detect or from env)
            compute_type: Override compute type (None = auto-detect)
        """
        from dalston.engine_sdk.model_storage import MultiSourceModelStorage

        model_storage = MultiSourceModelStorage.from_env()

        preload = os.environ.get("DALSTON_MODEL_PRELOAD")

        return cls(
            device=device,
            compute_type=compute_type,
            model_storage=model_storage,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=preload,
        )
