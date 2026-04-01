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
    Transcript,
    TranscriptionRequest,
    VocabularyMethod,
    VocabularySupport,
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

    ENGINE_ID = "faster-whisper"

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
        is_shared = self._core is not None
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
            shared_core=is_shared,
        )

    def transcribe_v1(
        self, audio: np.ndarray, params: TranscriptionRequest
    ) -> Transcript:
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
            # RT chunks are short VAD segments — no silence gap detection needed
            hallucination_silence_threshold=None,
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

        return self.build_transcript_from_core_result(
            result,
            language=result.language,
            alignment_method=AlignmentMethod.ATTENTION,
        )

    def get_models(self) -> list[str]:
        """Return list of supported model variants."""
        return FasterWhisperInference.SUPPORTED_MODELS

    def get_vocabulary_support(self):
        """Faster-whisper uses prompt conditioning (initial_prompt) in both modes."""
        return VocabularySupport(
            method=VocabularyMethod.PROMPT_CONDITIONING,
            batch=True,
            realtime=True,
        )

    def health_check(self) -> dict[str, Any]:
        """Return health status including model and GPU info."""
        return {
            **super().health_check(),
            "device": self._core.device if self._core else "unknown",
            "compute_type": self._core.compute_type if self._core else "unknown",
        }


if __name__ == "__main__":
    import asyncio

    engine = FasterWhisperRealtimeEngine()
    asyncio.run(engine.run())
