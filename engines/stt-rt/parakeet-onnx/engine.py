"""Real-time Parakeet ONNX streaming transcription engine.

Uses ONNX Runtime via the onnx-asr library for low-latency transcription
of VAD-segmented utterances. The SessionHandler provides VAD-based chunking,
and this engine transcribes each utterance using Parakeet CTC/TDT/RNNT via ONNX.

Unlike the NeMo-based real-time engine, this uses ONNX Runtime which
doesn't support native streaming but works well with VAD-chunked audio.
The tradeoff is simpler deployment (no NeMo/PyTorch) at the cost of
slightly higher per-utterance latency.

M44: Uses NeMoOnnxModelManager for dynamic model loading. A single container
can serve any Parakeet ONNX model variant without rebuild.

Environment variables:
    DALSTON_INSTANCE: Unique identifier for this worker (required)
    DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
    DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DALSTON_MODEL_TTL_SECONDS: Idle model TTL in seconds (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cpu.
    DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Defaults to none.
"""

import os
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.managers import NeMoOnnxModelManager
from dalston.realtime_sdk import (
    AsyncModelManager,
    RealtimeEngine,
    TranscribeResult,
    Word,
)

logger = structlog.get_logger()


class ParakeetOnnxStreamingEngine(RealtimeEngine):
    """Real-time transcription using Parakeet via ONNX Runtime with dynamic model loading.

    M44: Models are loaded on-demand using NeMoOnnxModelManager with TTL-based eviction.
    This allows a single container to serve any Parakeet ONNX model variant.

    Uses VAD-chunked transcription (not native streaming). The SDK's
    SessionHandler accumulates audio until a speech endpoint, then
    calls transcribe() on the accumulated chunk.

    Supports CTC, TDT, and RNNT decoder variants.

    Environment variables:
        DALSTON_INSTANCE: Unique identifier for this worker (required)
        DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
        DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
        DALSTON_MODEL_TTL_SECONDS: Idle model TTL in seconds (default: 3600)
        DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
        DALSTON_MODEL_PRELOAD: Model to preload on startup (e.g., parakeet-onnx-tdt-0.6b-v3)
        DALSTON_DEVICE: Device to use (cuda, cpu). Defaults to cpu.
        DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Defaults to none.
    """

    # Default model when client doesn't specify
    DEFAULT_MODEL = "parakeet-onnx-ctc-0.6b"

    def __init__(self) -> None:
        """Initialize the engine."""
        super().__init__()

        # Device configuration
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        if requested_device == "cuda":
            self._device = "cuda"
        elif requested_device in ("", "auto", "cpu"):
            self._device = "cpu"
            if requested_device in ("", "auto"):
                try:
                    import onnxruntime as ort

                    if "CUDAExecutionProvider" in ort.get_available_providers():
                        self._device = "cuda"
                except ImportError:
                    pass
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

        # Quantization
        self._quantization = os.environ.get("DALSTON_QUANTIZATION", "none").lower()

        logger.info(
            "parakeet_onnx_engine_init",
            device=self._device,
            quantization=self._quantization,
        )

    def load_models(self) -> None:
        """Initialize NeMoOnnxModelManager with optional preloading.

        M44: Models are loaded on-demand, not all at once. This method sets up
        the NeMoOnnxModelManager and optionally preloads a default model.
        """
        logger.info(
            "initializing_nemo_onnx_model_manager",
            device=self._device,
            quantization=self._quantization,
        )

        # Create sync model manager
        sync_manager = NeMoOnnxModelManager(
            device=self._device,
            quantization=self._quantization,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        # Wrap in async manager
        self._model_manager = AsyncModelManager(sync_manager)

        logger.info(
            "nemo_onnx_model_manager_initialized",
            max_loaded=sync_manager.max_loaded,
            ttl_seconds=sync_manager.ttl_seconds,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment.

        M44: Acquires the requested model (loading if needed), transcribes,
        then releases the model reference.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (ignored - Parakeet is English-only)
            model_variant: Model name (e.g., "parakeet-onnx-tdt-0.6b-v3")
            vocabulary: List of terms to boost recognition (not supported for ONNX)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        if self._model_manager is None:
            raise RuntimeError(
                "Model manager not initialized. Call load_models() first."
            )

        # Use default if no model specified
        model_id = model_variant or self.DEFAULT_MODEL

        # Normalize model ID
        model_id = self._normalize_model_id(model_id)

        if vocabulary:
            logger.debug(
                "vocabulary_not_supported_onnx",
                message="Vocabulary boosting not supported for ONNX engine. Terms ignored.",
                terms_count=len(vocabulary),
            )

        if language != "auto" and language != "en":
            logger.warning(
                "language_not_supported",
                requested=language,
                using="en",
            )

        # Note: transcribe is called from sync context in SessionHandler
        # We need to use the sync manager directly since we're in a sync method
        sync_manager = self._model_manager.manager

        model = sync_manager.acquire(model_id)
        try:
            return self._transcribe_with_model(model, audio)
        finally:
            sync_manager.release(model_id)

    def _normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to NeMoOnnxModelManager supported format.

        Args:
            model_id: Model identifier from client

        Returns:
            Normalized model ID
        """
        # Handle various naming formats
        mappings = {
            # Full names
            "parakeet-onnx-ctc-0.6b": "parakeet-onnx-ctc-0.6b",
            "parakeet-onnx-ctc-1.1b": "parakeet-onnx-ctc-1.1b",
            "parakeet-onnx-tdt-0.6b-v2": "parakeet-onnx-tdt-0.6b-v2",
            "parakeet-onnx-tdt-0.6b-v3": "parakeet-onnx-tdt-0.6b-v3",
            "parakeet-onnx-rnnt-0.6b": "parakeet-onnx-rnnt-0.6b",
            # Short variants (from legacy per-model containers)
            "ctc-0.6b": "ctc-0.6b",
            "ctc-1.1b": "ctc-1.1b",
            "tdt-0.6b-v2": "tdt-0.6b-v2",
            "tdt-0.6b-v3": "tdt-0.6b-v3",
            "rnnt-0.6b": "rnnt-0.6b",
        }
        return mappings.get(model_id, model_id)

    def _transcribe_with_model(
        self,
        model,
        audio: np.ndarray,
    ) -> TranscribeResult:
        """Perform transcription with the given model.

        Args:
            model: The onnx-asr model to use
            audio: Audio samples as float32 numpy array

        Returns:
            TranscribeResult
        """
        # Prepare audio for onnx-asr (expects float32 numpy array)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        # Transcribe using onnx-asr
        result = model.recognize(audio, sample_rate=16000)

        # Extract text
        if hasattr(result, "text"):
            text = str(result.text).strip()
        else:
            text = str(result).strip()

        if not text:
            return TranscribeResult(
                text="",
                words=[],
                language="en",
                confidence=1.0,
            )

        # Extract word timestamps if available
        words: list[Word] = []
        if hasattr(result, "words") and result.words:
            for w in result.words:
                word_text = str(w.word if hasattr(w, "word") else w.text).strip()
                if word_text:
                    words.append(
                        Word(
                            word=word_text,
                            start=float(w.start),
                            end=float(w.end),
                            confidence=0.95,
                        )
                    )

        return TranscribeResult(
            text=text,
            words=words,
            language="en",
            confidence=1.0,
        )

    def supports_streaming(self) -> bool:
        """ONNX models don't support native streaming (use VAD-chunked mode)."""
        return False

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers.

        M44: Returns all models the NeMoOnnxModelManager can load dynamically.
        """
        return [
            "parakeet-onnx-ctc-0.6b",
            "parakeet-onnx-ctc-1.1b",
            "parakeet-onnx-tdt-0.6b-v2",
            "parakeet-onnx-tdt-0.6b-v3",
            "parakeet-onnx-rnnt-0.6b",
        ]

    def get_languages(self) -> list[str]:
        """Return list of supported languages."""
        return ["en"]

    def get_runtime(self) -> str:
        """Return the inference framework identifier."""
        return "nemo-onnx"

    def get_supports_vocabulary(self) -> bool:
        """Return whether this engine supports vocabulary boosting."""
        return False

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
        """Return health status including model and device info."""
        base_health = super().health_check()

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
            "quantization": self._quantization,
        }


if __name__ == "__main__":
    import asyncio

    engine = ParakeetOnnxStreamingEngine()
    asyncio.run(engine.run())
