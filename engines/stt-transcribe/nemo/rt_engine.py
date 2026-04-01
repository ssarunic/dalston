"""Real-time Parakeet streaming transcription engine.

Uses NVIDIA NeMo Parakeet FastConformer with cache-aware streaming
for low-latency real-time transcription. Achieves ~100ms end-to-end
latency with native word-level timestamps.

Delegates inference to NemoInference (shared with the batch engine).
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
    DALSTON_RNNT_BUFFER_SECS: Total audio buffer for BatchedFrameASRRNNT (default: 4.0)
"""

import os
from collections.abc import Iterator
from typing import Any

import numpy as np
import structlog
import torch

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Transcript,
    TranscriptionRequest,
    TranscriptWord,
)
from dalston.engine_sdk.inference.nemo_inference import NemoInference
from dalston.realtime_sdk import AsyncModelManager
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine

logger = structlog.get_logger()


class NemoRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time streaming transcription using Parakeet with dynamic model loading.

    Delegates inference to NemoInference, which is shared with the batch
    Parakeet engine. The RT adapter handles:
    - Model ID normalization
    - VAD-chunked audio input (numpy arrays)
    - Output formatting to Transcript

    When run standalone, creates its own NemoInference in load_models().
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

    def __init__(self, core: NemoInference | None = None) -> None:
        """Initialize the engine.

        Args:
            core: Optional shared NemoInference. If provided, load_models()
                  skips creating its own core and uses the injected one.
        """
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "nemo")
        super().__init__()
        self._core: NemoInference | None = core

        # M71/M72: Cache-aware streaming configuration
        self._rnnt_chunk_ms = int(os.environ.get("DALSTON_RNNT_CHUNK_MS", "160"))
        self._rnnt_buffer_secs = float(
            os.environ.get("DALSTON_RNNT_BUFFER_SECS", "4.0")
        )

    def load_models(self) -> None:
        """Initialize NemoInference with optional preloading.

        If a NemoInference was injected via __init__, this method uses it
        instead of creating a new one. This is how the unified runner shares
        a single model instance between batch and RT adapters.
        """
        is_shared = self._core is not None
        if self._core is None:
            # Standalone mode — create own core
            self._core = NemoInference.from_env()

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

    def transcribe_v1(
        self, audio: np.ndarray, params: TranscriptionRequest
    ) -> Transcript:
        """Transcribe an audio segment via shared NemoInference.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            params: Typed transcriber parameters for this utterance

        Returns:
            Transcript with text, words, language, confidence
        """
        if self._core is None:
            raise RuntimeError(
                "NemoInference not initialized — call load_models() first"
            )

        # Use default if no model specified
        model_id = params.loaded_model_id or self.DEFAULT_MODEL
        language = params.language or "auto"
        vocabulary = params.vocabulary

        # Normalize model ID
        model_id = self._normalize_model_id(model_id)

        # Vocabulary boosting not yet implemented for real-time Parakeet.
        #
        # Implementation plan for RT vocabulary boosting:
        # 1. GPU-PB requires model.change_decoding_strategy() which needs
        #    direct model access via manager.acquire().
        # 2. The RT path currently delegates to NemoInference.transcribe()
        #    which doesn't expose the model object.
        # 3. To implement: add a vocabulary-aware transcribe path in
        #    NemoInference that acquires the model, applies GPU-PB config
        #    (same as batch_engine._configure_vocabulary_boosting), runs
        #    inference, resets decoding strategy, and releases.
        # 4. Need to evaluate latency impact of change_decoding_strategy()
        #    per utterance vs caching the boosting tree across utterances
        #    with the same vocabulary (session-scoped vocabulary is stable).
        # 5. Default boosting parameters (context_score=1.0,
        #    depth_scaling=2.0, boosting_tree_alpha=0.5) should be validated
        #    for RT utterance lengths (typically shorter than batch).
        if vocabulary:
            logger.debug(
                "vocabulary_not_supported_realtime",
                message="Vocabulary boosting not yet implemented for real-time Parakeet. Terms ignored.",
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
            engine_id="nemo",
            language_confidence=1.0 if language != "auto" else 0.5,
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
        return self._core.supports_native_streaming_decode(model_id)

    def transcribe_streaming(
        self,
        audio_iter: Iterator[np.ndarray],
        language: str,
        model_variant: str,
    ) -> Iterator[Transcript]:
        """Yield incremental Transcripts from cache-aware streaming.

        Each yielded result contains a single word with its timing.
        The SessionHandler should send each as a partial transcript event.

        Args:
            audio_iter: Iterator of float32 audio chunks
            language: Language code
            model_variant: Model name

        Yields:
            Transcript for each newly decoded word
        """
        if self._core is None:
            raise RuntimeError(
                "NemoInference not initialized — call load_models() first"
            )

        model_id = self._normalize_model_id(model_variant or self.DEFAULT_MODEL)

        logger.info(
            "streaming_decode_start",
            model_id=model_id,
            decoder_type=self._core.decoder_type(model_id),
            chunk_ms=self._rnnt_chunk_ms,
        )

        for word_result in self._core.transcribe_streaming(
            audio_iter,
            model_id,
            chunk_ms=self._rnnt_chunk_ms,
            buffer_secs=self._rnnt_buffer_secs,
        ):
            confidence = word_result.confidence or 0.95
            yield self.build_transcript(
                text=word_result.word,
                segments=[
                    self.build_segment(
                        start=word_result.start,
                        end=word_result.end,
                        text=word_result.word,
                        words=[
                            self.build_word(
                                text=word_result.word,
                                start=word_result.start,
                                end=word_result.end,
                                confidence=confidence,
                            )
                        ],
                        confidence=confidence,
                    )
                ],
                language=language if language != "auto" else "en",
                engine_id="nemo",
                language_confidence=confidence,
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
            "nemotron-streaming-rnnt-0.6b": "nemotron-streaming-rnnt-0.6b",
            # Short variants
            "0.6b": "parakeet-rnnt-0.6b",
            "1.1b": "parakeet-rnnt-1.1b",
            "rnnt-0.6b": "parakeet-rnnt-0.6b",
            "rnnt-1.1b": "parakeet-rnnt-1.1b",
            "ctc-0.6b": "parakeet-ctc-0.6b",
            "ctc-1.1b": "parakeet-ctc-1.1b",
            "tdt-0.6b-v3": "parakeet-tdt-0.6b-v3",
            "tdt-1.1b": "parakeet-tdt-1.1b",
            "nemotron-0.6b": "nemotron-streaming-rnnt-0.6b",
            # NGC / HuggingFace model IDs
            "nvidia/parakeet-rnnt-0.6b": "parakeet-rnnt-0.6b",
            "nvidia/parakeet-rnnt-1.1b": "parakeet-rnnt-1.1b",
            "nvidia/parakeet-ctc-0.6b": "parakeet-ctc-0.6b",
            "nvidia/parakeet-ctc-1.1b": "parakeet-ctc-1.1b",
            "nvidia/parakeet-tdt-0.6b-v3": "parakeet-tdt-0.6b-v3",
            "nvidia/parakeet-tdt-1.1b": "parakeet-tdt-1.1b",
            "nvidia/nemotron-speech-streaming-en-0.6b": "nemotron-streaming-rnnt-0.6b",
        }
        return mappings.get(model_id, model_id)

    def supports_native_streaming(self) -> bool:
        """RNNT/TDT models support real-time partial results via periodic batch inference."""
        return True

    def get_streaming_decode_fn(self, model_variant: str | None = None) -> Any:
        """Return a streaming decode callable for cache-aware models, else None.

        **Nemotron** (``nemotron-streaming-rnnt-0.6b``) was trained with limited
        right context and supports ``BatchedFrameASRRNNT(stateful_decoding=True)``.
        Returning ``self.transcribe_streaming`` here activates the session
        handler's per-chunk streaming loop, which feeds audio chunks directly to
        the decoder and emits partial transcript events during speech.

        **Offline Parakeet RNNT/TDT** (``parakeet-rnnt-*``, ``parakeet-tdt-*``)
        require the full audio context before decoding.  Returning ``None`` keeps
        them on the VAD-accumulate path, which buffers the utterance and
        transcribes at ``speech_end``.  Exposing the incomplete stream as a
        streaming decode fn would break utterance boundary detection: VAD
        ``speech_end`` flushes would find an empty buffer because the sentinel
        hasn't been pushed yet.

        CTC models always return ``None`` (cannot stream).
        """
        if self._core is None:
            return None
        model_id = self._normalize_model_id(model_variant or self.DEFAULT_MODEL)
        if self._core.is_cache_aware_streaming(model_id):
            return self.transcribe_streaming
        return None

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers."""
        return NemoInference.SUPPORTED_MODELS

    def get_engine_id(self) -> str:
        """Return the inference framework identifier."""
        return self._engine_id

    def get_vocabulary_support(self):
        """NeMo supports GPU-PB phrase boosting in batch only (RT not yet implemented)."""
        from dalston.common.pipeline_types import VocabularyMethod, VocabularySupport

        return VocabularySupport(
            method=VocabularyMethod.PHRASE_BOOSTING,
            batch=True,
            realtime=False,
        )

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

    engine = NemoRealtimeEngine()
    asyncio.run(engine.run())
