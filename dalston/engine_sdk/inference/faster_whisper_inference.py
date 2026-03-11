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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog

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
    vad_filter: bool = True
    word_timestamps: bool = True
    temperature: float | list[float] = 0.0
    task: str = "transcribe"
    initial_prompt: str | None = None
    hotwords: str | None = None


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
        if device is not None and compute_type is not None:
            self._device = device
            self._compute_type = compute_type
        else:
            self._device, self._compute_type = self._detect_device()

        self._manager = FasterWhisperModelManager(
            device=self._device,
            compute_type=self._compute_type,
            model_storage=model_storage,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
            preload=preload,
        )

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

    # -- Device detection ----------------------------------------------------

    @staticmethod
    def _detect_device() -> tuple[str, str]:
        """Detect the best available device and compute type.

        Returns:
            Tuple of (device, compute_type)
        """
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        if requested_device == "cpu":
            logger.info(
                "using_cpu_device",
                message="Running on CPU with int8 compute",
            )
            return "cpu", "int8"

        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass

        if requested_device == "cuda":
            raise RuntimeError(
                "DALSTON_DEVICE=cuda but CUDA is not available for faster-whisper."
            )

        if requested_device not in ("", "auto"):
            raise ValueError(
                f"Unknown DALSTON_DEVICE value: {requested_device}. Use cuda or cpu."
            )

        logger.info(
            "cuda_not_available",
            message="CUDA not available, falling back to CPU with int8 compute",
        )
        return "cpu", "int8"

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
        model = self._manager.acquire(model_id)
        try:
            return self._transcribe_with_model(model, audio, config)
        finally:
            self._manager.release(model_id)

    def _transcribe_with_model(
        self,
        model: WhisperModel,
        audio: str | Path | np.ndarray,
        config: FasterWhisperConfig,
    ) -> TranscriptionResult:
        """Execute transcription with an already-acquired model."""
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

        # Build kwargs for faster-whisper
        transcribe_kwargs: dict = {
            "language": language,
            "beam_size": config.beam_size,
            "vad_filter": config.vad_filter,
            "word_timestamps": config.word_timestamps,
            "temperature": config.temperature,
        }

        if config.task in {"transcribe", "translate"}:
            transcribe_kwargs["task"] = config.task

        if config.initial_prompt:
            transcribe_kwargs["initial_prompt"] = config.initial_prompt

        if config.hotwords:
            transcribe_kwargs["hotwords"] = config.hotwords

        # Run inference
        segments_generator, info = model.transcribe(
            audio_input,
            **transcribe_kwargs,
        )

        # Collect results into neutral types
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

        return TranscriptionResult(
            segments=segments,
            language=info.language,
            language_probability=info.language_probability,
            duration=info.duration,
        )

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
        # Configure S3 storage if bucket is set
        model_storage = None
        s3_bucket = os.environ.get("DALSTON_S3_BUCKET")
        if s3_bucket:
            from dalston.engine_sdk.model_storage import S3ModelStorage

            model_storage = S3ModelStorage.from_env()
            logger.info("s3_model_storage_enabled", bucket=s3_bucket)

        # Don't preload when S3 storage is enabled
        preload = None if model_storage else os.environ.get("DALSTON_MODEL_PRELOAD")

        return cls(
            device=device,
            compute_type=compute_type,
            model_storage=model_storage,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=preload,
        )
