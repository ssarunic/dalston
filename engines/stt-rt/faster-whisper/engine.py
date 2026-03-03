"""Real-time Whisper streaming transcription engine.

Uses faster-whisper for transcription with Silero VAD for speech detection.
Supports dynamic model loading via ModelManager (M43).
"""

import os
from typing import Any

import numpy as np
import structlog
from faster_whisper import WhisperModel

from dalston.engine_sdk.managers import FasterWhisperModelManager
from dalston.realtime_sdk import (
    AsyncModelManager,
    RealtimeEngine,
    TranscribeResult,
    Word,
)

logger = structlog.get_logger()


class WhisperStreamingEngine(RealtimeEngine):
    """Real-time streaming transcription using Whisper with dynamic model loading.

    Models are loaded on-demand using ModelManager with TTL-based eviction.
    This allows a single container to serve any model variant without restart.

    Environment variables:
        DALSTON_WORKER_ID: Unique identifier for this worker (required)
        DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
        DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 2)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
        DALSTON_MODEL_TTL_SECONDS: Idle model TTL in seconds (default: 3600)
        DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
        DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
        DALSTON_S3_BUCKET: S3 bucket for model storage (optional)
    """

    # Default model when client doesn't specify
    DEFAULT_MODEL = "large-v3-turbo"

    def __init__(self) -> None:
        """Initialize the engine."""
        super().__init__()
        self._device: str = "cpu"
        self._compute_type: str = "int8"

    def load_models(self) -> None:
        """Initialize model manager with optional preloading.

        Models are loaded on-demand, not all at once. This method sets up
        the ModelManager and optionally preloads a default model.
        """
        # Detect device
        self._device, self._compute_type = self._detect_device()
        logger.info(
            "using_device", device=self._device, compute_type=self._compute_type
        )

        # Create sync model manager
        sync_manager = FasterWhisperModelManager(
            device=self._device,
            compute_type=self._compute_type,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        # Wrap in async manager
        self._model_manager = AsyncModelManager(sync_manager)

        logger.info(
            "model_manager_initialized",
            max_loaded=sync_manager.max_loaded,
            ttl_seconds=sync_manager.ttl_seconds,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available device and compute type.

        Returns:
            Tuple of (device, compute_type)
        """
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass

        # Fallback to CPU
        return "cpu", "int8"

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment.

        Acquires the requested model (loading if needed), transcribes,
        then releases the model reference.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (e.g., "en") or "auto" for detection
            model_variant: Model name (e.g., "large-v3-turbo")
            vocabulary: List of terms to boost recognition (hotwords)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        # Use default if no model specified
        model_id = model_variant or self.DEFAULT_MODEL

        # Map common names to faster-whisper supported names
        model_id = self._normalize_model_id(model_id)

        if self._model_manager is None:
            raise RuntimeError("Model manager not initialized")

        # Note: transcribe is called from sync context in SessionHandler
        # We need to use the sync manager directly since we're in a sync method
        sync_manager = self._model_manager.manager

        model = sync_manager.acquire(model_id)
        try:
            return self._transcribe_with_model(model, audio, language, vocabulary)
        finally:
            sync_manager.release(model_id)

    def _normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to faster-whisper supported format.

        Args:
            model_id: Model identifier from client

        Returns:
            Normalized model ID
        """
        # Map legacy/alternative names to standard names
        mappings = {
            "faster-whisper-large-v3": "large-v3",
            "faster-whisper-large-v3-turbo": "large-v3-turbo",
            "faster-whisper-distil-large-v3": "distil-large-v3",
            "whisper-large-v3": "large-v3",
            "whisper-large-v3-turbo": "large-v3-turbo",
        }
        return mappings.get(model_id, model_id)

    def _transcribe_with_model(
        self,
        model: WhisperModel,
        audio: np.ndarray,
        language: str,
        vocabulary: list[str] | None,
    ) -> TranscribeResult:
        """Perform transcription with the given model.

        Args:
            model: The WhisperModel to use
            audio: Audio samples as float32 numpy array
            language: Language code or "auto"
            vocabulary: Optional vocabulary terms

        Returns:
            TranscribeResult
        """
        # Handle language
        lang = None if language == "auto" else language

        # Build transcription kwargs
        transcribe_kwargs: dict = {
            "language": lang,
            "beam_size": 5,
            "vad_filter": False,  # We handle VAD separately
            "word_timestamps": True,
        }

        # Add vocabulary as initial_prompt if provided
        if vocabulary:
            transcribe_kwargs["initial_prompt"] = ", ".join(vocabulary)
            logger.debug(
                "vocabulary_enabled",
                terms=vocabulary[:5],
                total_terms=len(vocabulary),
            )

        # Transcribe
        segments, info = model.transcribe(
            audio,
            **transcribe_kwargs,
        )

        # Collect results
        words: list[Word] = []
        text_parts: list[str] = []

        for segment in segments:
            text_parts.append(segment.text.strip())

            # Extract word-level timestamps
            if segment.words:
                for word in segment.words:
                    words.append(
                        Word(
                            word=word.word.strip(),
                            start=word.start,
                            end=word.end,
                            confidence=word.probability,
                        )
                    )

        return TranscribeResult(
            text=" ".join(text_parts),
            words=words,
            language=info.language,
            confidence=info.language_probability,
        )

    def get_models(self) -> list[str]:
        """Return list of supported model variants.

        These are the canonical names clients can request.
        """
        return [
            "large-v3-turbo",
            "large-v3",
            "distil-large-v3",
            "large-v2",
            "medium",
            "small",
            "base",
            "tiny",
        ]

    def get_languages(self) -> list[str]:
        """Return list of supported languages.

        Whisper supports 99 languages, we return "auto" to indicate
        all are supported with auto-detection.
        """
        return ["auto"]

    def get_engine(self) -> str:
        """Return engine type identifier."""
        return "faster-whisper"

    def get_supports_vocabulary(self) -> bool:
        """Return True - faster-whisper supports vocabulary via initial_prompt."""
        return True

    def get_gpu_memory_usage(self) -> str:
        """Return GPU memory usage string."""
        try:
            import torch

            if torch.cuda.is_available():
                used = torch.cuda.memory_allocated() / 1e9
                return f"{used:.1f}GB"
        except ImportError:
            pass
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        """Return health status including model and GPU info."""
        base_health = super().health_check()

        # Add engine-specific info
        cuda_available = False
        cuda_device_count = 0

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            pass

        # Get model manager stats
        model_stats = {}
        if self._model_manager is not None:
            model_stats = self._model_manager.get_stats()

        return {
            **base_health,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "max_loaded": model_stats.get("max_loaded", 0),
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
        }


if __name__ == "__main__":
    import asyncio

    engine = WhisperStreamingEngine()
    asyncio.run(engine.run())
