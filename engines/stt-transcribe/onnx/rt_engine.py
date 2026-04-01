"""Real-time ONNX streaming transcription engine.

Uses ONNX Runtime via the onnx-asr library for low-latency transcription
of VAD-segmented utterances. Delegates inference to OnnxInference
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

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Transcript,
    TranscriptionRequest,
    TranscriptWord,
    VocabularySupport,
)
from dalston.engine_sdk.inference.onnx_inference import OnnxInference
from dalston.realtime_sdk import AsyncModelManager
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine

logger = structlog.get_logger()


class OnnxRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time transcription using Parakeet via ONNX Runtime.

    Delegates inference to OnnxInference, which is shared with the batch
    ONNX engine. The RT adapter handles:
    - Model ID normalization
    - VAD-chunked audio input (numpy arrays)
    - Output formatting to Transcript

    When run standalone, creates its own OnnxInference in load_models().
    When used within a unified runner, accepts an injected core to share a
    single loaded model with the batch adapter.

    Supports CTC, TDT, and RNNT decoder variants.
    """

    ENGINE_ID = "onnx"

    # Default model when client doesn't specify
    DEFAULT_MODEL = "parakeet-onnx-ctc-0.6b"

    def __init__(self, core: OnnxInference | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared OnnxInference. If provided, load_models()
                  skips creating its own core and uses the injected one.
        """
        super().__init__()
        self._core: OnnxInference | None = core

    def load_models(self) -> None:
        """Initialize OnnxInference with optional preloading.

        If a OnnxInference was injected via __init__, this method uses it
        instead of creating a new one. This is how the unified runner shares
        a single model instance between batch and RT adapters.
        """
        is_shared = self._core is not None
        if self._core is None:
            # Standalone mode — create own core
            self._core = OnnxInference.from_env()

        # Wrap the core's manager in AsyncModelManager for heartbeat reporting
        self._model_manager = AsyncModelManager(self._core.manager)

        logger.info(
            "model_manager_initialized",
            max_loaded=self._core.manager.max_loaded,
            ttl_seconds=self._core.manager.ttl_seconds,
            device=self._core.device,
            quantization=self._core.quantization,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            shared_core=is_shared,
        )

    def transcribe_v1(
        self, audio: np.ndarray, params: TranscriptionRequest
    ) -> Transcript:
        """Transcribe an audio segment via shared OnnxInference.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            params: Typed transcriber parameters for this utterance

        Returns:
            Transcript with text, words, language, confidence
        """
        if self._core is None:
            raise RuntimeError(
                "OnnxInference not initialized — call load_models() first"
            )

        # Use default if no model specified
        model_id = params.loaded_model_id or self.DEFAULT_MODEL
        language = params.language or "auto"
        vocabulary = params.vocabulary

        # Normalize model ID
        model_id = self._normalize_model_id(model_id)

        if vocabulary:
            logger.debug(
                "vocabulary_not_supported_onnx",
                message="ONNX Runtime has no decoding graph manipulation API. "
                "Use the NeMo engine for vocabulary boosting with Parakeet models.",
                terms_count=len(vocabulary),
            )

        # Delegate to shared core
        result = self._core.transcribe(audio, model_id)

        # Format core result into Transcript
        segments = []
        text_parts: list[str] = []

        for seg in result.segments:
            words: list[TranscriptWord] = []
            seg_text = seg.text if hasattr(seg, "text") else ""
            text_parts.append(seg_text)
            for w in seg.words:
                words.append(
                    self.build_word(
                        text=w.word,
                        start=w.start,
                        end=w.end,
                        confidence=w.confidence or 0.95,
                        alignment_method=AlignmentMethod.CTC,
                    )
                )
            segments.append(
                self.build_segment(
                    start=seg.start
                    if hasattr(seg, "start")
                    else (words[0].start if words else 0.0),
                    end=seg.end
                    if hasattr(seg, "end")
                    else (words[-1].end if words else 0.0),
                    text=seg_text,
                    words=words if words else None,
                )
            )

        return self.build_transcript(
            text=result.text,
            segments=segments,
            language=language if language != "auto" else "en",
            engine_id=self.engine_id,
            language_confidence=1.0 if language != "auto" else 0.5,
        )

    def _normalize_model_id(self, model_id: str) -> str:
        """Normalize model ID to OnnxModelManager supported format."""
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

    def supports_native_streaming(self) -> bool:
        """ONNX models don't support native streaming (use VAD-chunked mode)."""
        return False

    def get_models(self) -> list[str]:
        """Return list of curated model aliases."""
        return OnnxInference.CURATED_MODELS

    def get_vocabulary_support(self):
        """ONNX Runtime has no vocabulary boosting mechanism.

        Parakeet models support phrase boosting when run on NeMo
        (which provides GPU-PB decoding graph manipulation), but ONNX
        Runtime does not expose decoding strategy APIs. Use the NeMo
        engine if vocabulary boosting is required.
        """
        return VocabularySupport()

    def health_check(self) -> dict[str, Any]:
        """Return health status including model and device info."""
        return {
            **super().health_check(),
            "device": self._core.device if self._core else "unknown",
            "quantization": self._core.quantization if self._core else "unknown",
        }


if __name__ == "__main__":
    import asyncio

    engine = OnnxRealtimeEngine()
    asyncio.run(engine.run())
