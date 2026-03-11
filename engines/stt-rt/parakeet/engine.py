"""Real-time Parakeet streaming transcription engine.

Uses NVIDIA NeMo Parakeet FastConformer with cache-aware streaming
for low-latency real-time transcription. Achieves ~100ms end-to-end
latency with native word-level timestamps.

Delegates inference to ParakeetCore (shared with the batch engine).
Supports dynamic model loading via NeMoModelManager (M44).

M71: RNNT/TDT models use cache-aware streaming inference to emit
tokens frame-by-frame. CTC models retain the VAD-accumulate path.

Environment variables:
    DALSTON_INSTANCE: Unique identifier for this worker (required)
    DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
    DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DALSTON_MODEL_TTL_SECONDS: Idle model TTL in seconds (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_RNNT_CHUNK_MS: Chunk duration in ms for streaming (default: 160)
"""

import os
from collections.abc import Iterator
from typing import Any

import numpy as np
import structlog
import torch

from dalston.engine_sdk.cores.parakeet_core import ParakeetCore
from dalston.realtime_sdk import (
    AsyncModelManager,
    RealtimeEngine,
    TranscribeResult,
    Word,
)

logger = structlog.get_logger()


class ParakeetStreamingEngine(RealtimeEngine):
    """Real-time streaming transcription using Parakeet with dynamic model loading.

    Delegates inference to ParakeetCore, which is shared with the batch
    Parakeet engine. The RT adapter handles:
    - Model ID normalization
    - VAD-chunked audio input (numpy arrays)
    - Output formatting to TranscribeResult

    When run standalone, creates its own ParakeetCore in load_models().
    When used within a unified runner, accepts an injected core to share a
    single loaded model with the batch adapter.

    Environment variables:
        DALSTON_INSTANCE: Unique identifier for this worker (required)
        DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
        DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
        DALSTON_MODEL_TTL_SECONDS: Idle model TTL in seconds (default: 3600)
        DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
        DALSTON_MODEL_PRELOAD: Model to preload on startup (e.g., parakeet-rnnt-1.1b)
        DALSTON_DEVICE: Device to use (cuda, cpu). Defaults to cuda if available.
    """

    # Default model when client doesn't specify
    DEFAULT_MODEL = "parakeet-rnnt-0.6b"

    def __init__(self, core: ParakeetCore | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared ParakeetCore. If provided, load_models()
                  skips creating its own core and uses the injected one.
        """
        super().__init__()
        self._core: ParakeetCore | None = core

        # M71: Cache-aware streaming configuration
        self._rnnt_chunk_ms = int(
            os.environ.get("DALSTON_RNNT_CHUNK_MS", "160")
        )

    def load_models(self) -> None:
        """Initialize ParakeetCore with optional preloading.

        If a ParakeetCore was injected via __init__, this method uses it
        instead of creating a new one. This is how the unified runner shares
        a single model instance between batch and RT adapters.
        """
        is_shared = self._core is not None
        if self._core is None:
            # Standalone mode — create own core
            self._core = ParakeetCore.from_env()

        # Wrap the core's manager in AsyncModelManager for heartbeat reporting
        self._model_manager = AsyncModelManager(self._core.manager)

        logger.info(
            "model_manager_initialized",
            max_loaded=self._core.manager.max_loaded,
            ttl_seconds=self._core.manager.ttl_seconds,
            device=self._core.device,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            shared_core=is_shared,
            rnnt_chunk_ms=self._rnnt_chunk_ms,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment via shared ParakeetCore.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (ignored - Parakeet is English-only)
            model_variant: Model name (e.g., "parakeet-rnnt-1.1b")
            vocabulary: List of terms to boost recognition (not yet supported)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        if self._core is None:
            raise RuntimeError(
                "ParakeetCore not initialized — call load_models() first"
            )

        # Use default if no model specified
        model_id = model_variant or self.DEFAULT_MODEL

        # Normalize model ID
        model_id = self._normalize_model_id(model_id)

        # Vocabulary boosting not yet implemented for real-time Parakeet
        if vocabulary:
            logger.debug(
                "vocabulary_not_supported_realtime",
                message="Vocabulary boosting not yet implemented for real-time Parakeet. Terms ignored.",
                terms_count=len(vocabulary),
            )

        # Parakeet is English-only, ignore language parameter
        if language != "auto" and language != "en":
            logger.warning(
                "language_not_supported",
                requested=language,
                using="en",
            )

        # Delegate to shared core
        result = self._core.transcribe(audio, model_id)

        # Format core result into RT output contract
        words: list[Word] = []
        for seg in result.segments:
            for w in seg.words:
                words.append(
                    Word(
                        word=w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.confidence or 0.95,
                    )
                )

        return TranscribeResult(
            text=result.text,
            words=words,
            language="en",
            confidence=1.0,
        )

    def use_streaming_decode(self, model_variant: str | None = None) -> bool:
        """Check whether the given model should use streaming decode.

        Returns True when the model's decoder architecture supports
        cache-aware streaming (RNNT or TDT). CTC models always return False.

        Args:
            model_variant: Model name; uses DEFAULT_MODEL if None.

        Returns:
            True if streaming decode should be used for this model.
        """
        if self._core is None:
            return False

        model_id = self._normalize_model_id(model_variant or self.DEFAULT_MODEL)
        return self._core.supports_streaming_decode(model_id)

    def transcribe_streaming(
        self,
        audio_iter: Iterator[np.ndarray],
        language: str,
        model_variant: str,
    ) -> Iterator[TranscribeResult]:
        """Yield incremental TranscribeResults from cache-aware streaming.

        Each yielded result contains a single word with its timing.
        The SessionHandler should send each as a partial transcript event.

        Args:
            audio_iter: Iterator of float32 audio chunks
            language: Language code (ignored — Parakeet is English-only)
            model_variant: Model name

        Yields:
            TranscribeResult for each newly decoded word
        """
        if self._core is None:
            raise RuntimeError(
                "ParakeetCore not initialized — call load_models() first"
            )

        model_id = self._normalize_model_id(model_variant or self.DEFAULT_MODEL)

        logger.info(
            "streaming_decode_start",
            model_id=model_id,
            decoder_type=self._core.decoder_type(model_id),
            chunk_ms=self._rnnt_chunk_ms,
        )

        for word_result in self._core.transcribe_streaming(
            audio_iter, model_id, chunk_ms=self._rnnt_chunk_ms
        ):
            yield TranscribeResult(
                text=word_result.word,
                words=[
                    Word(
                        word=word_result.word,
                        start=word_result.start,
                        end=word_result.end,
                        confidence=word_result.confidence or 0.95,
                    )
                ],
                language="en",
                confidence=word_result.confidence or 0.95,
            )

    def _normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to NeMoModelManager supported format.

        Args:
            model_id: Model identifier from client

        Returns:
            Normalized model ID
        """
        mappings = {
            # Full names
            "parakeet-rnnt-0.6b": "parakeet-rnnt-0.6b",
            "parakeet-rnnt-1.1b": "parakeet-rnnt-1.1b",
            "parakeet-ctc-0.6b": "parakeet-ctc-0.6b",
            "parakeet-ctc-1.1b": "parakeet-ctc-1.1b",
            "parakeet-tdt-0.6b-v3": "parakeet-tdt-0.6b-v3",
            "parakeet-tdt-1.1b": "parakeet-tdt-1.1b",
            # Short variants
            "0.6b": "parakeet-rnnt-0.6b",
            "1.1b": "parakeet-rnnt-1.1b",
            "rnnt-0.6b": "parakeet-rnnt-0.6b",
            "rnnt-1.1b": "parakeet-rnnt-1.1b",
            "ctc-0.6b": "parakeet-ctc-0.6b",
            "ctc-1.1b": "parakeet-ctc-1.1b",
            "tdt-0.6b-v3": "parakeet-tdt-0.6b-v3",
            "tdt-1.1b": "parakeet-tdt-1.1b",
            # NGC model IDs
            "nvidia/parakeet-rnnt-0.6b": "parakeet-rnnt-0.6b",
            "nvidia/parakeet-rnnt-1.1b": "parakeet-rnnt-1.1b",
            "nvidia/parakeet-ctc-0.6b": "parakeet-ctc-0.6b",
            "nvidia/parakeet-ctc-1.1b": "parakeet-ctc-1.1b",
            "nvidia/parakeet-tdt-0.6b-v3": "parakeet-tdt-0.6b-v3",
            "nvidia/parakeet-tdt-1.1b": "parakeet-tdt-1.1b",
        }
        return mappings.get(model_id, model_id)

    def supports_streaming(self) -> bool:
        """Parakeet supports native streaming with partial results."""
        return True

    def get_streaming_decode_fn(
        self, model_variant: str | None = None
    ) -> Any:
        """Return streaming decode callback for RNNT/TDT models.

        M71: When the model supports streaming decode, returns
        ``self.transcribe_streaming`` so the SessionHandler can feed
        audio chunks directly to the streaming decoder.

        Returns None for CTC models, causing the SessionHandler to
        use the VAD-accumulate path.
        """
        if self.use_streaming_decode(model_variant):
            return self.transcribe_streaming
        return None

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers."""
        return ParakeetCore.SUPPORTED_MODELS

    def get_languages(self) -> list[str]:
        """Return list of supported languages. Parakeet only supports English."""
        return ["en"]

    def get_runtime(self) -> str:
        """Return the inference framework identifier."""
        return "nemo"

    def get_supports_vocabulary(self) -> bool:
        """Return whether this engine supports vocabulary boosting."""
        return False

    def get_gpu_memory_usage(self) -> str:
        """Return GPU memory usage string."""
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            return f"{used:.1f}GB"
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        """Return health status including model and GPU info."""
        base_health = super().health_check()

        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0
        cuda_memory_total = 0

        if cuda_available:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        # Get model manager stats
        model_stats = {}
        if self._model_manager is not None:
            model_stats = self._model_manager.get_stats()

        device = self._core.device if self._core else "unknown"

        return {
            **base_health,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "max_loaded": model_stats.get("max_loaded", 0),
            "device": device,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }


if __name__ == "__main__":
    import asyncio

    engine = ParakeetStreamingEngine()
    asyncio.run(engine.run())
