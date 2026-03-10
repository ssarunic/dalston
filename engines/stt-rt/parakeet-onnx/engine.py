"""Real-time Parakeet ONNX streaming transcription engine.

Uses ONNX Runtime via the onnx-asr library for low-latency transcription
of VAD-segmented utterances. Delegates inference to ParakeetOnnxCore
(shared with the batch engine).

Unlike the NeMo-based real-time engine, this uses ONNX Runtime which
doesn't support native streaming but works well with VAD-chunked audio.
The tradeoff is simpler deployment (no NeMo/PyTorch) at the cost of
slightly higher per-utterance latency.

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

from dalston.engine_sdk.cores.parakeet_onnx_core import ParakeetOnnxCore
from dalston.realtime_sdk import (
    AsyncModelManager,
    RealtimeEngine,
    TranscribeResult,
    Word,
)

logger = structlog.get_logger()


class ParakeetOnnxStreamingEngine(RealtimeEngine):
    """Real-time transcription using Parakeet via ONNX Runtime.

    Delegates inference to ParakeetOnnxCore, which is shared with the batch
    ONNX engine. The RT adapter handles:
    - Model ID normalization
    - VAD-chunked audio input (numpy arrays)
    - Output formatting to TranscribeResult

    When run standalone, creates its own ParakeetOnnxCore in load_models().
    When used within a unified runner, accepts an injected core to share a
    single loaded model with the batch adapter.

    Supports CTC, TDT, and RNNT decoder variants.
    """

    # Default model when client doesn't specify
    DEFAULT_MODEL = "parakeet-onnx-ctc-0.6b"

    def __init__(self, core: ParakeetOnnxCore | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared ParakeetOnnxCore. If provided, load_models()
                  skips creating its own core and uses the injected one.
        """
        super().__init__()
        self._core: ParakeetOnnxCore | None = core

    def load_models(self) -> None:
        """Initialize ParakeetOnnxCore with optional preloading.

        If a ParakeetOnnxCore was injected via __init__, this method uses it
        instead of creating a new one. This is how the unified runner shares
        a single model instance between batch and RT adapters.
        """
        if self._core is None:
            # Standalone mode — create own core
            self._core = ParakeetOnnxCore.from_env()

        # Wrap the core's manager in AsyncModelManager for heartbeat reporting
        self._model_manager = AsyncModelManager(self._core.manager)

        logger.info(
            "model_manager_initialized",
            max_loaded=self._core.manager.max_loaded,
            ttl_seconds=self._core.manager.ttl_seconds,
            device=self._core.device,
            quantization=self._core.quantization,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            shared_core=self._core is not None,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment via shared ParakeetOnnxCore.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (ignored - Parakeet is English-only)
            model_variant: Model name (e.g., "parakeet-onnx-tdt-0.6b-v3")
            vocabulary: List of terms to boost recognition (not supported for ONNX)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        if self._core is None:
            raise RuntimeError(
                "ParakeetOnnxCore not initialized — call load_models() first"
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

    def _normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to NeMoOnnxModelManager supported format."""
        mappings = {
            # Full names
            "parakeet-onnx-ctc-0.6b": "parakeet-onnx-ctc-0.6b",
            "parakeet-onnx-ctc-1.1b": "parakeet-onnx-ctc-1.1b",
            "parakeet-onnx-tdt-0.6b-v2": "parakeet-onnx-tdt-0.6b-v2",
            "parakeet-onnx-tdt-0.6b-v3": "parakeet-onnx-tdt-0.6b-v3",
            "parakeet-onnx-rnnt-0.6b": "parakeet-onnx-rnnt-0.6b",
            # Short variants
            "ctc-0.6b": "ctc-0.6b",
            "ctc-1.1b": "ctc-1.1b",
            "tdt-0.6b-v2": "tdt-0.6b-v2",
            "tdt-0.6b-v3": "tdt-0.6b-v3",
            "rnnt-0.6b": "rnnt-0.6b",
        }
        return mappings.get(model_id, model_id)

    def supports_streaming(self) -> bool:
        """ONNX models don't support native streaming (use VAD-chunked mode)."""
        return False

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers."""
        return ParakeetOnnxCore.SUPPORTED_MODELS

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

        model_stats = {}
        if self._model_manager is not None:
            model_stats = self._model_manager.get_stats()

        device = self._core.device if self._core else "unknown"
        quantization = self._core.quantization if self._core else "unknown"

        return {
            **base_health,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "max_loaded": model_stats.get("max_loaded", 0),
            "device": device,
            "quantization": quantization,
        }


if __name__ == "__main__":
    import asyncio

    engine = ParakeetOnnxStreamingEngine()
    asyncio.run(engine.run())
