"""vLLM Audio ASR engine for audio-capable LLMs.

Uses vLLM to serve audio-capable language models for transcription.
Supports Voxtral (Mistral) and Qwen2-Audio model families via
model-specific adapters.

Audio LLMs produce text-only output without timestamps. If word-level
timing is required, the orchestrator chains the alignment stage after
this engine: vllm-asr (transcribe) -> phoneme-align (add timestamps).

Features:
    - TTL-based model management with LRU eviction
    - Model-specific adapters for prompt building and output parsing
    - Runtime model swapping via config["loaded_model_id"]
    - GPU-only inference (vLLM requires CUDA)

Environment variables:
    DALSTON_ENGINE_ID: Engine ID for registration (default: "vllm-asr")
    DALSTON_DEFAULT_MODEL: Default HF model ID (default: "mistralai/Voxtral-Mini-3B-2507")
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 7200)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 1)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    DALSTON_VLLM_GPU_MEMORY_UTILIZATION: GPU memory fraction for vLLM (default: 0.9)
    DALSTON_VLLM_MAX_MODEL_LEN: Maximum model context length (default: 4096)
    DALSTON_S3_BUCKET: S3 bucket for model storage (enables S3-backed model loading)
"""

from __future__ import annotations

import gc
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
import torch

if TYPE_CHECKING:
    from dalston.engine_sdk.model_storage import MultiSourceModelStorage

from dalston.common.pipeline_types import (
    Transcript,
    TranscriptionRequest,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineCapabilities,
    TaskRequest,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.vllm_asr.inference import (
    transcribe_audio_array,
    transcribe_audio_path,
)

logger = structlog.get_logger()


class VllmAsrBatchEngine(BaseBatchTranscribeEngine):
    """vLLM-based ASR engine for audio-capable LLMs.

    This engine uses vLLM to serve audio LLMs (Voxtral, Qwen2-Audio) for
    transcription. Model-specific adapters handle prompt construction and
    output parsing for each model family.

    The engine manages model lifecycle directly (load/unload/swap) rather
    than using the ModelManager base class, because vLLM has its own
    GPU memory management that conflicts with external model managers.

    GPU is required - vLLM does not support CPU inference.
    """

    ENGINE_ID = "vllm-asr"
    DEFAULT_MODEL = "mistralai/Voxtral-Mini-3B-2507"

    def __init__(
        self,
        llm: Any = None,
        on_model_loaded: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the engine.

        Args:
            llm: Optional shared vLLM LLM instance. If provided, the engine
                 skips creating its own and uses the injected one. This is how
                 the unified runner shares a single model across batch and RT.
            on_model_loaded: Optional callback invoked after a model swap
                 completes. Receives the new model ID. Used by the unified
                 runner to reconfigure admission limits.
        """
        super().__init__()

        self._llm = llm
        self._on_model_loaded = on_model_loaded
        self._tokenizer = None
        self._model_storage: MultiSourceModelStorage | None = None

        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL", self.DEFAULT_MODEL
        )

        # When a shared LLM is injected (unified runner), the model is already
        # loaded — record its ID so _ensure_model_loaded short-circuits.
        self._loaded_model_id: str | None = (
            self._default_model_id if llm is not None else None
        )
        self._loaded_model_path: str | None = None

        # vLLM configuration
        self._gpu_memory_utilization = float(
            os.environ.get("DALSTON_VLLM_GPU_MEMORY_UTILIZATION", "0.9")
        )
        self._max_model_len = int(os.environ.get("DALSTON_VLLM_MAX_MODEL_LEN", "4096"))

        # Configure model storage from environment
        from dalston.engine_sdk.model_storage import MultiSourceModelStorage

        self._model_storage = MultiSourceModelStorage.from_env()

        # Verify GPU is available using pynvml instead of torch.cuda to avoid
        # premature CUDA initialization.  vLLM's EngineCore uses forked
        # subprocesses; if CUDA is initialized in the parent first the fork
        # fails with "Cannot re-initialize CUDA in forked subprocess".
        try:
            import pynvml

            pynvml.nvmlInit()
            gpu_count = pynvml.nvmlDeviceGetCount()
            pynvml.nvmlShutdown()
        except Exception as exc:
            raise RuntimeError(
                "vLLM-ASR requires CUDA. GPU not available on this system."
            ) from exc

        self.logger.info(
            "engine_init",
            engine_id=self.engine_id,
            default_model=self._default_model_id,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            gpu_count=gpu_count,
            s3_storage_enabled=self._model_storage is not None,
        )

    def _ensure_model_loaded(self, loaded_model_id: str) -> None:
        """Ensure the requested model is loaded, swapping if necessary.

        If S3ModelStorage is configured, models are downloaded from S3 to
        local cache first. Otherwise, vLLM downloads directly from HuggingFace.

        Args:
            loaded_model_id: HuggingFace model identifier

        Raises:
            ValueError: If no adapter exists for the model
            RuntimeError: If vLLM is not installed or model loading fails
            ModelNotInS3Error: If S3 storage is enabled but model is not in S3
        """
        if loaded_model_id == self._loaded_model_id:
            return

        # Unload current model if one is loaded
        if self._llm is not None:
            self.logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=loaded_model_id,
            )
            self._set_runtime_state(status="unloading")

            del self._llm
            self._llm = None
            self._loaded_model_id = None
            self._loaded_model_path = None
            self._tokenizer = None

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

            self.logger.info("model_unloaded")

        # Determine model path - either from storage or HuggingFace ID
        model_path: str = loaded_model_id
        if self._model_storage is not None:
            self.logger.info(
                "ensuring_model_from_storage",
                loaded_model_id=loaded_model_id,
            )
            local_path = self._model_storage.ensure_local(loaded_model_id)
            model_path = str(local_path)
            self.logger.info(
                "model_ready_from_storage",
                loaded_model_id=loaded_model_id,
                local_path=model_path,
            )

        # Load the requested model
        self._set_runtime_state(status="loading")
        self.logger.info(
            "loading_vllm_model",
            loaded_model_id=loaded_model_id,
            model_path=model_path,
        )

        try:
            from vllm import LLM
        except ImportError as e:
            self._set_runtime_state(loaded_model=None, status="error")
            raise RuntimeError(
                "vLLM not installed. Install with: pip install 'vllm[audio]>=0.6.0'"
            ) from e

        self._llm = LLM(
            model=model_path,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            limit_mm_per_prompt={"audio": 1},
            allowed_local_media_path=tempfile.gettempdir(),
        )

        self._loaded_model_id = loaded_model_id
        self._loaded_model_path = model_path
        self._set_runtime_state(loaded_model=loaded_model_id, status="idle")

        self.logger.info(
            "model_loaded_successfully",
            loaded_model_id=loaded_model_id,
            model_path=model_path,
        )

        if self._on_model_loaded is not None:
            self._on_model_loaded(loaded_model_id)

    def transcribe_audio(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio using a vLLM audio LLM.

        Args:
            task_request: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            Transcript with text and segments
        """
        audio_path = task_request.audio_path
        params = task_request.get_transcribe_params()
        language = params.language
        return self._transcribe_with_vllm(
            loaded_model_id=params.loaded_model_id or self._default_model_id,
            language=language,
            vocabulary=params.vocabulary,
            channel=params.channel,
            audio_path=audio_path,
        )

    def transcribe_audio_array(
        self,
        audio: np.ndarray,
        params: TranscriptionRequest,
        sample_rate: int = 16000,
    ) -> Transcript:
        """Transcribe in-memory audio buffers via vLLM.

        This is a shared migration bridge for realtime workers that operate on
        numpy chunks instead of file inputs.
        """
        language = params.language
        return self._transcribe_with_vllm(
            loaded_model_id=params.loaded_model_id or self._default_model_id,
            language=language,
            vocabulary=params.vocabulary,
            channel=params.channel,
            audio=audio,
            sample_rate=sample_rate,
        )

    def _transcribe_with_vllm(
        self,
        loaded_model_id: str,
        language: str | None,
        vocabulary: list[str] | None,
        channel: int | None,
        *,
        audio_path: os.PathLike[str] | str | None = None,
        audio: np.ndarray | None = None,
        sample_rate: int = 16000,
    ) -> Transcript:
        """Common vLLM transcription path for file and in-memory audio."""
        if language == "auto" or language == "":
            language = None

        warnings: list[str] = []
        if vocabulary:
            self.logger.info(
                "vocabulary_via_instruction",
                terms_count=len(vocabulary),
                loaded_model_id=loaded_model_id,
            )

        self._ensure_model_loaded(loaded_model_id)
        self._set_runtime_state(loaded_model=loaded_model_id, status="processing")

        try:
            if audio_path is not None:
                self.logger.info(
                    "transcribing",
                    audio_path=str(audio_path),
                    loaded_model_id=loaded_model_id,
                    language=language,
                )
                raw_text, transcript = transcribe_audio_path(
                    llm=self._llm,
                    audio_path=Path(audio_path),
                    language=language,
                    vocabulary=vocabulary,
                )
            elif audio is not None:
                self.logger.info(
                    "transcribing_audio_array",
                    loaded_model_id=loaded_model_id,
                    language=language,
                    sample_rate=sample_rate,
                    sample_count=int(audio.size),
                )
                raw_text, transcript = transcribe_audio_array(
                    llm=self._llm,
                    audio=audio,
                    language=language,
                    sample_rate=sample_rate,
                    vocabulary=vocabulary,
                )
            else:
                raise ValueError("Either audio_path or audio must be provided")

            self.logger.info("transcription_complete", char_count=len(raw_text))

            transcript.engine_id = self.engine_id
            if channel is not None:
                transcript.channel = channel
            if warnings:
                transcript.warnings = warnings + list(transcript.warnings)

            return transcript
        finally:
            self._set_runtime_state(loaded_model=loaded_model_id, status="idle")

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU and model info."""
        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0.0
        cuda_memory_total = 0.0

        if cuda_available and cuda_device_count > 0:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        return {
            "status": "healthy",
            "engine_id": self.engine_id,
            "model_loaded": self._llm is not None,
            "loaded_model_id": self._loaded_model_id,
            "loaded_model_path": self._loaded_model_path,
            "supported_models": [],
            "s3_storage_enabled": self._model_storage is not None,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
            "gpu_memory_utilization": self._gpu_memory_utilization,
        }

    def get_capabilities(self) -> EngineCapabilities:
        """Return vLLM-ASR engine capabilities."""
        return EngineCapabilities(
            engine_id=self.engine_id,
            version="1.0.0",
            stages=["transcribe"],
            supports_word_timestamps=False,  # Audio LLMs don't produce timestamps
            supports_native_streaming=False,
            model_variants=[],
            gpu_required=True,
            gpu_vram_mb=8000,  # Minimum for smallest model
            supports_cpu=False,
            min_ram_gb=16,
            rtf_gpu=0.15,
        )

    def shutdown(self) -> None:
        """Shutdown engine and release GPU resources."""
        self.logger.info("engine_shutdown")
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._loaded_model_id = None
            self._loaded_model_path = None

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

        super().shutdown()


if __name__ == "__main__":
    engine = VllmAsrBatchEngine()
    engine.run()
