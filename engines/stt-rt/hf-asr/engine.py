"""Real-time HuggingFace Transformers ASR engine.

Uses HuggingFace Transformers ASR pipelines for VAD-chunked real-time
transcription. Delegates model lifecycle to HFTransformersModelManager.

When run standalone, creates its own model manager in load_models().
When used within a unified runner, accepts an injected manager to share
loaded models with the batch adapter.

Environment variables:
    DALSTON_INSTANCE: Unique identifier for this worker (required)
    DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
    DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    DALSTON_DEFAULT_MODEL_ID: Default HF model ID (default: openai/whisper-large-v3)
    DALSTON_DEVICE: Device for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import structlog
import torch

from dalston.common.pipeline_types import (
    AlignmentMethod,
    TranscribeInput,
    Transcript,
    TranscriptWord,
)
from dalston.engine_sdk.managers import HFTransformersModelManager
from dalston.realtime_sdk import AsyncModelManager
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine

logger = structlog.get_logger()


class HfAsrRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time transcription using HuggingFace Transformers ASR pipelines.

    Supports any model with pipeline_tag=automatic-speech-recognition on
    HuggingFace Hub (Whisper, Wav2Vec2, HuBERT, MMS, community fine-tunes).

    When run standalone, creates its own HFTransformersModelManager in
    load_models(). When used within a unified runner, accepts an injected
    manager to share loaded models with the batch adapter.
    """

    DEFAULT_MODEL_ID = "openai/whisper-large-v3"

    def __init__(self, manager: HFTransformersModelManager | None = None) -> None:
        """Initialize the engine.

        Args:
            manager: Optional shared HFTransformersModelManager. If provided,
                     load_models() skips creating its own manager.
        """
        super().__init__()

        self._manager: HFTransformersModelManager | None = manager
        self._model_manager: AsyncModelManager | None = None

        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "hf-asr")
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )

        logger.info(
            "hf_asr_rt_engine_init",
            engine_id=self._engine_id,
            default_model_id=self._default_model_id,
            shared_manager=manager is not None,
        )

    def _detect_device(self) -> tuple[str, torch.dtype]:
        """Detect the best available device and dtype."""
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        if requested_device == "cpu":
            return "cpu", torch.float32

        if torch.cuda.is_available():
            return "cuda", torch.float16

        if requested_device == "cuda":
            raise RuntimeError("DALSTON_DEVICE=cuda but CUDA is not available.")

        if requested_device not in ("", "auto"):
            raise ValueError(
                f"Unknown DALSTON_DEVICE value: {requested_device}. Use cuda or cpu."
            )

        return "cpu", torch.float32

    def load_models(self) -> None:
        """Initialize HFTransformersModelManager with optional preloading.

        If a manager was injected via __init__, this method uses it
        instead of creating a new one. This is how the unified runner shares
        a single model manager between batch and RT adapters.
        """
        is_shared = self._manager is not None
        if self._manager is None:
            device, torch_dtype = self._detect_device()
            self._manager = HFTransformersModelManager(
                device=device,
                torch_dtype=torch_dtype,
                ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
                max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
                preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            )

        self._model_manager = AsyncModelManager(self._manager)

        logger.info(
            "model_manager_initialized",
            max_loaded=self._manager.max_loaded,
            ttl_seconds=self._manager.ttl_seconds,
            device=self._manager.device,
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            shared_manager=is_shared,
        )

    def transcribe_v1(self, audio: np.ndarray, params: TranscribeInput) -> Transcript:
        """Transcribe one VAD-segmented utterance using a HuggingFace ASR pipeline.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            params: Typed transcriber parameters for this utterance

        Returns:
            Transcript with text, words, and language
        """
        if self._manager is None:
            raise RuntimeError(
                "HFTransformersModelManager not initialized — call load_models() first"
            )

        model_id = params.loaded_model_id or self._default_model_id
        language = params.language
        if language == "" or language == "auto":
            language = None

        warnings: list[str] = []
        if params.vocabulary:
            logger.debug(
                "vocabulary_not_supported",
                terms_count=len(params.vocabulary),
            )
            warnings.append(
                f"Vocabulary boosting ({len(params.vocabulary)} terms) "
                "not supported by hf-asr"
            )

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        pipe = self._manager.acquire(model_id)
        try:
            # Build pipeline kwargs
            pipe_kwargs: dict[str, Any] = {
                "return_timestamps": "word",
            }
            generate_kwargs: dict[str, Any] = {}
            if language:
                generate_kwargs["language"] = language
            if generate_kwargs:
                pipe_kwargs["generate_kwargs"] = generate_kwargs

            # HF pipeline expects dict with raw waveform for numpy input
            result = pipe(
                {"raw": audio, "sampling_rate": 16000},
                **pipe_kwargs,
            )

            transcript = self._normalize_output(result, language)
            transcript.engine_id = self._engine_id
            transcript.channel = params.channel
            if warnings:
                transcript.warnings = warnings + list(transcript.warnings)

            return transcript
        finally:
            self._manager.release(model_id)

    def _normalize_output(
        self,
        result: dict[str, Any],
        language: str | None,
    ) -> Transcript:
        """Normalize HuggingFace pipeline output to Transcript.

        HF pipeline returns different formats based on model architecture:
        - Whisper: {"text": "...", "chunks": [{"text": "...", "timestamp": (start, end)}]}
        - Wav2Vec2/MMS: {"text": "..."} (no timestamps)
        """
        text = result.get("text", "").strip()
        chunks = result.get("chunks", [])

        segments = []
        has_word_timestamps = False

        if chunks:
            words: list[TranscriptWord] = []
            for chunk in chunks:
                chunk_text = chunk.get("text", "").strip()
                if not chunk_text:
                    continue

                timestamp = chunk.get("timestamp", (None, None))
                start = timestamp[0] if timestamp and timestamp[0] is not None else 0.0
                end = timestamp[1] if timestamp and timestamp[1] is not None else 0.0

                words.append(
                    self.build_word(
                        text=chunk_text,
                        start=round(start, 3),
                        end=round(end, 3),
                        confidence=None,
                        alignment_method=AlignmentMethod.ATTENTION,
                    )
                )

            has_word_timestamps = bool(words)

            if words:
                segments.append(
                    self.build_segment(
                        start=round(words[0].start, 3),
                        end=round(words[-1].end, 3),
                        text=text,
                        words=words,
                    )
                )
            else:
                segments.append(self.build_segment(start=0.0, end=0.0, text=text))
        else:
            segments.append(self.build_segment(start=0.0, end=0.0, text=text))

        return self.build_transcript(
            text=text,
            segments=segments,
            language=language or "auto",
            engine_id=self._engine_id,
            alignment_method=(
                AlignmentMethod.ATTENTION
                if has_word_timestamps
                else AlignmentMethod.UNKNOWN
            ),
        )

    def supports_streaming(self) -> bool:
        """HF ASR pipelines don't support native streaming (use VAD-chunked mode)."""
        return False

    def get_models(self) -> list[str]:
        return [self._default_model_id]

    def get_languages(self) -> list[str]:
        return ["all"]

    def get_engine_id(self) -> str:
        return self._engine_id

    def get_supports_vocabulary(self) -> bool:
        return False

    def get_gpu_memory_usage(self) -> str:
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            return f"{used:.1f}GB"
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        base_health = super().health_check()

        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0.0
        cuda_memory_total = 0.0

        if cuda_available and cuda_device_count > 0:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        model_stats = {}
        if self._model_manager is not None:
            model_stats = self._model_manager.get_stats()

        device = self._manager.device if self._manager else "unknown"

        return {
            **base_health,
            "models_loaded": model_stats.get("loaded_models", []),
            "model_count": model_stats.get("model_count", 0),
            "max_loaded": model_stats.get("max_loaded", 0),
            "device": device,
            "engine_id": self._engine_id,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }

    def shutdown(self) -> None:
        logger.info("hf_asr_rt_shutdown")
        if self._manager is not None:
            self._manager.shutdown()
        super().shutdown()


if __name__ == "__main__":
    import asyncio

    engine = HfAsrRealtimeEngine()
    asyncio.run(engine.run())
