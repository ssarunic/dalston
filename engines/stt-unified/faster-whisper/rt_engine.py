"""Real-time Whisper streaming transcription engine.

Uses faster-whisper for transcription with Silero VAD for speech detection.
Delegates inference to FasterWhisperInference (shared with the batch engine).
Supports dynamic model loading via ModelManager (M43).
"""

import os
from typing import Any

import numpy as np
import structlog

from dalston.common.pipeline_types import (
    AlignmentMethod,
    TranscribeInput,
    Transcript,
    TranscriptWord,
)
from dalston.engine_sdk.inference.faster_whisper_inference import (
    FasterWhisperConfig,
    FasterWhisperInference,
)
from dalston.realtime_sdk import AsyncModelManager
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine

logger = structlog.get_logger()


class FasterWhisperRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time streaming transcription using Whisper with dynamic model loading.

    Delegates inference to FasterWhisperInference, which is shared with the batch
    faster-whisper engine. The RT adapter handles:
    - Model ID normalization
    - VAD-chunked audio input (numpy arrays)
    - Output formatting to Transcript

    When run standalone, creates its own FasterWhisperInference in load_models().
    When used within a unified runner, accepts an injected core to share a
    single loaded model with the batch adapter.

    Environment variables:
        DALSTON_INSTANCE: Unique identifier for this worker (required)
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

    def __init__(self, core: FasterWhisperInference | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared FasterWhisperInference. If provided, load_models()
                  skips creating its own core and uses the injected one.
        """
        super().__init__()
        self._core: FasterWhisperInference | None = core

    def load_models(self) -> None:
        """Initialize shared FasterWhisperInference with optional preloading.

        If a FasterWhisperInference was injected via __init__, this method uses it
        instead of creating a new one. This is how the unified runner shares
        a single model instance between batch and RT adapters.
        """
        if self._core is None:
            # Standalone mode — create own core
            self._core = FasterWhisperInference.from_env()

        # Wrap the core's manager in AsyncModelManager for heartbeat reporting
        self._model_manager = AsyncModelManager(self._core.manager)

        logger.info(
            "model_manager_initialized",
            max_loaded=self._core.manager.max_loaded,
            ttl_seconds=self._core.manager.ttl_seconds,
            device=self._core.device,
            compute_type=self._core.compute_type,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            s3_storage_enabled=self._core.manager.model_storage is not None,
            shared_core=self._core is not None,
        )

    def transcribe_v1(self, audio: np.ndarray, params: TranscribeInput) -> Transcript:
        """Transcribe an audio segment via shared FasterWhisperInference.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            params: Typed transcriber parameters for this utterance

        Returns:
            Transcript with text, words, language, confidence
        """
        if self._core is None:
            raise RuntimeError(
                "FasterWhisperInference not initialized — call load_models() first"
            )

        # Use default if no model specified
        model_id = params.loaded_model_id or self.DEFAULT_MODEL
        language = params.language or "auto"
        vocabulary = params.vocabulary

        # Build config for realtime use (VAD handled by SessionHandler)
        initial_prompt = ", ".join(vocabulary) if vocabulary else None
        config = FasterWhisperConfig(
            language=language,
            beam_size=params.beam_size if params.beam_size is not None else 5,
            vad_filter=False,  # VAD handled separately by SessionHandler
            word_timestamps=(
                True if params.word_timestamps is None else params.word_timestamps
            ),
            temperature=params.temperature,
            task=params.task,
            initial_prompt=initial_prompt,
        )

        if vocabulary:
            logger.debug(
                "vocabulary_enabled",
                terms=vocabulary[:5],
                total_terms=len(vocabulary),
            )

        # Delegate to shared core
        result = self._core.transcribe(
            audio=audio,
            model_id=model_id,
            config=config,
        )

        # Format core result into Transcript
        segments = []
        text_parts: list[str] = []

        for seg in result.segments:
            words: list[TranscriptWord] = []
            text_parts.append(seg.text)
            for w in seg.words:
                words.append(
                    self.build_word(
                        text=w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.probability,
                        alignment_method=AlignmentMethod.ATTENTION,
                    )
                )
            segments.append(
                self.build_segment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    words=words if words else None,
                )
            )

        return self.build_transcript(
            text=" ".join(text_parts),
            segments=segments,
            language=result.language,
            engine_id="faster-whisper",
            language_confidence=result.language_probability,
            alignment_method=AlignmentMethod.ATTENTION,
        )

    def get_models(self) -> list[str]:
        """Return list of supported model variants."""
        return FasterWhisperInference.SUPPORTED_MODELS

    def get_engine_id(self) -> str:
        """Return the inference framework identifier."""
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

        device = self._core.device if self._core else "unknown"
        compute_type = self._core.compute_type if self._core else "unknown"

        return {
            **base_health,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "max_loaded": model_stats.get("max_loaded", 0),
            "device": device,
            "compute_type": compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
        }


if __name__ == "__main__":
    import asyncio

    engine = FasterWhisperRealtimeEngine()
    asyncio.run(engine.run())
