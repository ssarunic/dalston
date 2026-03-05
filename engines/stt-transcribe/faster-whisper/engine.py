"""Faster-Whisper transcription engine with TTL-based model management.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.

Features:
    - Runtime model swapping via config["runtime_model_id"]
    - TTL-based model eviction for idle models
    - LRU eviction when at max_loaded capacity
    - Multi-model support on single GPU

Environment variables:
    DALSTON_RUNTIME: Runtime engine ID for registration (default: "faster-whisper")
    DALSTON_DEFAULT_MODEL_ID: Default model ID (default: "large-v3-turbo")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    WHISPER_MODELS_DIR: Directory for model cache (default: /models/ctranslate2/faster-whisper)
"""

import os
from typing import Any

from dalston.engine_sdk import (
    AlignmentMethod,
    Engine,
    Segment,
    TaskInput,
    TaskOutput,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)
from dalston.engine_sdk.managers import FasterWhisperModelManager


class WhisperEngine(Engine):
    """Faster-Whisper transcription engine with TTL-based model management.

    This engine uses FasterWhisperModelManager to handle model lifecycle:
    - Models are loaded on first request for that model
    - Multiple models can be loaded simultaneously (up to max_loaded)
    - Idle models are evicted after TTL expires
    - When at capacity, least-recently-used models are evicted first

    Automatically detects GPU availability and selects appropriate compute type:
    - GPU (CUDA): float16 for maximum performance
    - CPU: int8 for efficient inference (all models including large-v3-turbo)
    """

    DEFAULT_BEAM_SIZE = 5
    DEFAULT_VAD_FILTER = True
    DEFAULT_MODEL_ID = "large-v3-turbo"

    def __init__(self) -> None:
        super().__init__()

        # Get configuration from environment
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )
        self._runtime = os.environ.get("DALSTON_RUNTIME", "faster-whisper")

        # Auto-detect device and compute type
        self._device, self._compute_type = self._detect_device()

        # Configure S3 storage if bucket is set
        model_storage = None
        s3_bucket = os.environ.get("DALSTON_S3_BUCKET")
        if s3_bucket:
            from dalston.engine_sdk.model_storage import S3ModelStorage

            model_storage = S3ModelStorage.from_env()
            self.logger.info("s3_model_storage_enabled", bucket=s3_bucket)

        # Initialize model manager with TTL eviction
        self._manager = FasterWhisperModelManager(
            device=self._device,
            compute_type=self._compute_type,
            model_storage=model_storage,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        self.logger.info(
            "engine_init",
            runtime=self._runtime,
            default_model=self._default_model_id,
            device=self._device,
            compute_type=self._compute_type,
            ttl_seconds=self._manager.ttl_seconds,
            max_loaded=self._manager.max_loaded,
        )

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available device and compute type.

        Returns:
            Tuple of (device, compute_type)
        """
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        if requested_device == "cpu":
            self.logger.info(
                "using_cpu_device",
                message="Running on CPU with int8 compute - inference will be slower than GPU",
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

        self.logger.info(
            "cuda_not_available",
            message="CUDA not available, falling back to CPU with int8 compute",
        )
        return "cpu", "int8"

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Faster-Whisper.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text, segments, and language
        """
        audio_path = input.audio_path
        config = input.config

        # Handle language: None or "auto" means auto-detect
        language = config.get("language")
        if language == "auto" or language == "":
            language = None
        beam_size = config.get("beam_size", self.DEFAULT_BEAM_SIZE)
        vad_filter = config.get("vad_filter", self.DEFAULT_VAD_FILTER)
        channel = config.get("channel")
        vocabulary = config.get("vocabulary")

        # Get model to use from task config
        runtime_model_id = config.get("runtime_model_id", self._default_model_id)

        # Acquire model from manager (loads if needed, updates LRU)
        model = self._manager.acquire(runtime_model_id)
        try:
            # Update runtime state for heartbeat reporting
            self._set_runtime_state(loaded_model=runtime_model_id, status="processing")

            self.logger.info("transcribing", audio_path=str(audio_path))
            self.logger.info(
                "transcribe_config",
                runtime_model_id=runtime_model_id,
                language=language,
                beam_size=beam_size,
                vad_filter=vad_filter,
                vocabulary_terms=len(vocabulary) if vocabulary else 0,
            )

            # Build transcribe kwargs
            transcribe_kwargs: dict = {
                "language": language,
                "beam_size": beam_size,
                "vad_filter": vad_filter,
                "word_timestamps": True,
            }

            if vocabulary:
                transcribe_kwargs["hotwords"] = " ".join(vocabulary)
                self.logger.info(
                    "vocabulary_enabled",
                    terms=vocabulary[:5],
                    total_terms=len(vocabulary),
                )

            # Transcribe audio
            segments_generator, info = model.transcribe(
                str(audio_path),
                **transcribe_kwargs,
            )

            # Collect segments
            segments: list[Segment] = []
            full_text_parts: list[str] = []

            for segment in segments_generator:
                words: list[Word] | None = None
                if segment.words:
                    words = [
                        Word(
                            text=word.word.strip(),
                            start=round(word.start, 3),
                            end=round(word.end, 3),
                            confidence=round(word.probability, 3),
                            alignment_method=AlignmentMethod.ATTENTION,
                        )
                        for word in segment.words
                    ]

                segments.append(
                    Segment(
                        start=round(segment.start, 3),
                        end=round(segment.end, 3),
                        text=segment.text.strip(),
                        words=words,
                    )
                )
                full_text_parts.append(segment.text.strip())

            full_text = " ".join(full_text_parts)

            self.logger.info(
                "transcription_complete",
                segment_count=len(segments),
                char_count=len(full_text),
            )
            self.logger.info(
                "detected_language",
                language=info.language,
                confidence=round(info.language_probability, 2),
            )

            has_word_timestamps = any(seg.words for seg in segments)
            timestamp_granularity_actual = (
                TimestampGranularity.WORD
                if has_word_timestamps
                else TimestampGranularity.SEGMENT
            )

            output = TranscribeOutput(
                text=full_text,
                segments=segments,
                language=info.language,
                language_confidence=round(info.language_probability, 3),
                duration=info.duration,
                timestamp_granularity_requested=TimestampGranularity.WORD,
                timestamp_granularity_actual=timestamp_granularity_actual,
                alignment_method=(
                    AlignmentMethod.ATTENTION if has_word_timestamps else None
                ),
                channel=channel,
                runtime=self._runtime,
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return TaskOutput(data=output)

        finally:
            # Always release the model reference
            self._manager.release(runtime_model_id)
            self._set_runtime_state(status="idle")

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability and model stats."""
        cuda_available = False
        cuda_device_count = 0

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            pass

        manager_stats = self._manager.get_stats()

        return {
            "status": "healthy",
            "runtime": self._runtime,
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "model_manager": manager_stats,
        }

    def get_local_cache_stats(self) -> dict[str, Any] | None:
        """Get local model cache statistics for heartbeat reporting.

        Returns cache stats from S3ModelStorage if configured.
        """
        return self._manager.get_local_cache_stats()

    def shutdown(self) -> None:
        """Shutdown engine and cleanup resources."""
        self.logger.info("engine_shutdown")
        self._manager.shutdown()
        super().shutdown()


if __name__ == "__main__":
    engine = WhisperEngine()
    engine.run()
