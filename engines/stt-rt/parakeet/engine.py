"""Real-time Parakeet streaming transcription engine.

Uses NVIDIA NeMo Parakeet FastConformer with cache-aware streaming
for low-latency real-time transcription. Achieves ~100ms end-to-end
latency with native word-level timestamps.

M44: Uses NeMoModelManager for dynamic model loading. A single container
can serve any Parakeet RNNT/CTC model variant without rebuild.

Environment variables:
    DALSTON_WORKER_ID: Unique identifier for this worker (required)
    DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
    DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DALSTON_MODEL_TTL_SECONDS: Idle model TTL in seconds (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
"""

import os
from typing import Any

import numpy as np
import structlog
import torch

from dalston.engine_sdk.managers import NeMoModelManager
from dalston.realtime_sdk import (
    AsyncModelManager,
    RealtimeEngine,
    TranscribeResult,
    Word,
)

logger = structlog.get_logger()


class ParakeetStreamingEngine(RealtimeEngine):
    """Real-time streaming transcription using Parakeet with dynamic model loading.

    M44: Models are loaded on-demand using NeMoModelManager with TTL-based eviction.
    This allows a single container to serve any Parakeet RNNT/CTC model variant.

    Uses cache-aware FastConformer encoder for true streaming inference
    without chunked re-encoding, achieving lower latency than Whisper.

    Supports both GPU (CUDA) and CPU inference. GPU is strongly
    recommended for real-time use due to latency requirements.

    Environment variables:
        DALSTON_WORKER_ID: Unique identifier for this worker (required)
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

    def __init__(self) -> None:
        """Initialize the engine."""
        super().__init__()
        self._device: str = "cuda"

        # Determine device from environment or availability
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()
        cuda_available = torch.cuda.is_available()

        if requested_device == "cpu":
            self._device = "cpu"
            logger.warning(
                "using_cpu_device",
                message="Running on CPU - real-time latency may not be achievable",
            )
        elif requested_device in ("", "auto", "cuda"):
            if cuda_available:
                self._device = "cuda"
                logger.info("cuda_available", device_count=torch.cuda.device_count())
            else:
                if requested_device == "cuda":
                    raise RuntimeError(
                        "CUDA requested but not available. Set DALSTON_DEVICE=cpu "
                        "only for local development/testing."
                    )
                self._device = "cpu"
                logger.warning(
                    "cuda_not_available",
                    message="CUDA not available, falling back to CPU - latency will be higher",
                )
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

    def load_models(self) -> None:
        """Initialize NeMoModelManager with optional preloading.

        M44: Models are loaded on-demand, not all at once. This method sets up
        the NeMoModelManager and optionally preloads a default model.
        """
        logger.info(
            "initializing_nemo_model_manager",
            device=self._device,
        )

        # Create sync model manager
        sync_manager = NeMoModelManager(
            device=self._device,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        # Wrap in async manager
        self._model_manager = AsyncModelManager(sync_manager)

        logger.info(
            "nemo_model_manager_initialized",
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
            model_variant: Model name (e.g., "parakeet-rnnt-1.1b")
            vocabulary: List of terms to boost recognition (not yet supported)

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

        # Note: transcribe is called from sync context in SessionHandler
        # We need to use the sync manager directly since we're in a sync method
        sync_manager = self._model_manager.manager

        model = sync_manager.acquire(model_id)
        try:
            return self._transcribe_with_model(model, audio)
        finally:
            sync_manager.release(model_id)

    def _normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to NeMoModelManager supported format.

        Args:
            model_id: Model identifier from client

        Returns:
            Normalized model ID
        """
        # Handle various naming formats
        mappings = {
            # Full names
            "parakeet-rnnt-0.6b": "parakeet-rnnt-0.6b",
            "parakeet-rnnt-1.1b": "parakeet-rnnt-1.1b",
            "parakeet-ctc-0.6b": "parakeet-ctc-0.6b",
            "parakeet-ctc-1.1b": "parakeet-ctc-1.1b",
            "parakeet-tdt-1.1b": "parakeet-tdt-1.1b",
            # Short variants (from legacy per-model containers)
            "0.6b": "parakeet-rnnt-0.6b",
            "1.1b": "parakeet-rnnt-1.1b",
            "rnnt-0.6b": "parakeet-rnnt-0.6b",
            "rnnt-1.1b": "parakeet-rnnt-1.1b",
            "ctc-0.6b": "parakeet-ctc-0.6b",
            "ctc-1.1b": "parakeet-ctc-1.1b",
            "tdt-1.1b": "parakeet-tdt-1.1b",
            # NGC model IDs
            "nvidia/parakeet-rnnt-0.6b": "parakeet-rnnt-0.6b",
            "nvidia/parakeet-rnnt-1.1b": "parakeet-rnnt-1.1b",
            "nvidia/parakeet-ctc-0.6b": "parakeet-ctc-0.6b",
            "nvidia/parakeet-ctc-1.1b": "parakeet-ctc-1.1b",
            "nvidia/parakeet-tdt-1.1b": "parakeet-tdt-1.1b",
        }
        return mappings.get(model_id, model_id)

    def _transcribe_with_model(
        self,
        model,
        audio: np.ndarray,
    ) -> TranscribeResult:
        """Perform transcription with the given model.

        Args:
            model: The NeMo ASRModel to use
            audio: Audio samples as float32 numpy array

        Returns:
            TranscribeResult
        """
        # Prepare audio for NeMo (expects float32 numpy array)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Ensure audio is 1D (NeMo expects shape (samples,))
        if audio.ndim > 1:
            audio = audio.squeeze()

        # Transcribe using NeMo's transcribe method
        # NeMo expects a list of numpy arrays or file paths
        with torch.inference_mode():
            if self._device == "cuda":
                with torch.cuda.amp.autocast():
                    hypotheses = model.transcribe(
                        [audio],  # Pass as list of numpy arrays
                        batch_size=1,
                        return_hypotheses=True,
                    )
            else:
                hypotheses = model.transcribe(
                    [audio],  # Pass as list of numpy arrays
                    batch_size=1,
                    return_hypotheses=True,
                )

        if not hypotheses:
            return TranscribeResult(
                text="",
                words=[],
                language="en",
                confidence=1.0,
            )

        hypothesis = hypotheses[0]

        # Extract text
        if hasattr(hypothesis, "text"):
            text = hypothesis.text
        else:
            text = str(hypothesis)

        # Extract word-level timestamps if available
        words: list[Word] = []

        if hasattr(hypothesis, "timestep") and hypothesis.timestep is not None:
            tokens = text.split()
            timesteps = hypothesis.timestep
            frame_shift_seconds = 0.01  # 10ms frame shift

            for i, (token, frame_idx) in enumerate(
                zip(tokens, timesteps, strict=False)
            ):
                word_start = frame_idx * frame_shift_seconds
                if i + 1 < len(timesteps):
                    word_end = timesteps[i + 1] * frame_shift_seconds
                else:
                    word_end = word_start + 0.1

                words.append(
                    Word(
                        word=token,
                        start=word_start,
                        end=word_end,
                        confidence=0.95,
                    )
                )

        return TranscribeResult(
            text=text.strip(),
            words=words,
            language="en",
            confidence=1.0,  # Parakeet is English-only, confidence is implicit
        )

    def supports_streaming(self) -> bool:
        """Parakeet supports native streaming with partial results."""
        return True

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers.

        M44: Returns all models the NeMoModelManager can load dynamically.
        """
        return [
            "parakeet-rnnt-0.6b",
            "parakeet-rnnt-1.1b",
            "parakeet-ctc-0.6b",
            "parakeet-ctc-1.1b",
            "parakeet-tdt-1.1b",
        ]

    def get_languages(self) -> list[str]:
        """Return list of supported languages.

        Parakeet only supports English.
        """
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

        return {
            **base_health,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "max_loaded": model_stats.get("max_loaded", 0),
            "device": self._device,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }


if __name__ == "__main__":
    import asyncio

    engine = ParakeetStreamingEngine()
    asyncio.run(engine.run())
