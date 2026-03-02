"""vLLM Audio ASR engine for audio-capable LLMs.

Uses vLLM to serve audio-capable language models for transcription.
Supports Voxtral (Mistral) and Qwen2-Audio model families via
model-specific adapters.

Audio LLMs produce text-only output without timestamps. If word-level
timing is required, the orchestrator chains the alignment stage after
this engine: vllm-asr (transcribe) → phoneme-align (add timestamps).

Features:
    - TTL-based model management with LRU eviction
    - Model-specific adapters for prompt building and output parsing
    - Runtime model swapping via config["runtime_model_id"]
    - GPU-only inference (vLLM requires CUDA)

Environment variables:
    DALSTON_ENGINE_ID: Engine ID for registration (default: "vllm-asr")
    DALSTON_DEFAULT_MODEL_ID: Default HF model ID (default: "mistralai/Voxtral-Mini-3B-2507")
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 7200)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 1)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    DALSTON_VLLM_GPU_MEMORY_UTILIZATION: GPU memory fraction for vLLM (default: 0.9)
    DALSTON_VLLM_MAX_MODEL_LEN: Maximum model context length (default: 4096)
"""

from __future__ import annotations

import gc
import os
import sys
from typing import Any

import structlog
import torch

from dalston.engine_sdk import (
    Engine,
    EngineCapabilities,
    TaskInput,
    TaskOutput,
)

# Add engine directory to path for adapter imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adapters import ADAPTER_REGISTRY, get_adapter

logger = structlog.get_logger()


class VLLMASREngine(Engine):
    """vLLM-based ASR engine for audio-capable LLMs.

    This engine uses vLLM to serve audio LLMs (Voxtral, Qwen2-Audio) for
    transcription. Model-specific adapters handle prompt construction and
    output parsing for each model family.

    The engine manages model lifecycle directly (load/unload/swap) rather
    than using the ModelManager base class, because vLLM has its own
    GPU memory management that conflicts with external model managers.

    GPU is required - vLLM does not support CPU inference.
    """

    DEFAULT_MODEL_ID = "mistralai/Voxtral-Mini-3B-2507"

    def __init__(self) -> None:
        super().__init__()

        self._llm = None
        self._loaded_model_id: str | None = None
        self._tokenizer = None

        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "vllm-asr")
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )

        # vLLM configuration
        self._gpu_memory_utilization = float(
            os.environ.get("DALSTON_VLLM_GPU_MEMORY_UTILIZATION", "0.9")
        )
        self._max_model_len = int(
            os.environ.get("DALSTON_VLLM_MAX_MODEL_LEN", "4096")
        )

        # Verify CUDA is available
        if not torch.cuda.is_available():
            raise RuntimeError(
                "vLLM-ASR requires CUDA. GPU not available on this system."
            )

        self.logger.info(
            "engine_init",
            engine_id=self._engine_id,
            default_model=self._default_model_id,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            cuda_device_count=torch.cuda.device_count(),
        )

    def _ensure_model_loaded(self, runtime_model_id: str) -> None:
        """Ensure the requested model is loaded, swapping if necessary.

        Args:
            runtime_model_id: HuggingFace model identifier

        Raises:
            ValueError: If no adapter exists for the model
            RuntimeError: If vLLM is not installed or model loading fails
        """
        if runtime_model_id == self._loaded_model_id:
            return

        # Validate adapter exists before loading
        if runtime_model_id not in ADAPTER_REGISTRY:
            raise ValueError(
                f"No adapter for model: {runtime_model_id}. "
                f"Supported models: {sorted(ADAPTER_REGISTRY.keys())}"
            )

        # Unload current model if one is loaded
        if self._llm is not None:
            self.logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=runtime_model_id,
            )
            self._set_runtime_state(status="unloading")

            del self._llm
            self._llm = None
            self._loaded_model_id = None
            self._tokenizer = None

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

            self.logger.info("model_unloaded")

        # Load the requested model
        self._set_runtime_state(status="loading")
        self.logger.info(
            "loading_vllm_model",
            runtime_model_id=runtime_model_id,
        )

        try:
            from vllm import LLM
        except ImportError as e:
            self._set_runtime_state(loaded_model=None, status="error")
            raise RuntimeError(
                "vLLM not installed. Install with: pip install 'vllm[audio]>=0.6.0'"
            ) from e

        self._llm = LLM(
            model=runtime_model_id,
            trust_remote_code=True,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            limit_mm_per_prompt={"audio": 1},
        )

        self._loaded_model_id = runtime_model_id
        self._set_runtime_state(loaded_model=runtime_model_id, status="idle")

        self.logger.info(
            "model_loaded_successfully",
            runtime_model_id=runtime_model_id,
        )

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using a vLLM audio LLM.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text and segments
        """
        audio_path = input.audio_path
        config = input.config
        language = config.get("language")
        vocabulary = config.get("vocabulary")
        channel = config.get("channel")

        if language == "auto" or language == "":
            language = None

        # Warn about vocabulary - audio LLMs don't support vocabulary boosting
        warnings: list[str] = []
        if vocabulary:
            self.logger.warning(
                "vocabulary_not_supported",
                message="Vocabulary boosting is not supported for vLLM-ASR. Terms will be ignored.",
                terms_count=len(vocabulary),
            )
            warnings.append(
                f"Vocabulary boosting ({len(vocabulary)} terms) not supported by vLLM-ASR engine"
            )

        # Get model to use
        runtime_model_id = config.get("runtime_model_id", self._default_model_id)

        # Load model (with swapping if needed)
        self._ensure_model_loaded(runtime_model_id)

        # Get adapter for this model
        adapter = get_adapter(runtime_model_id)

        self._set_runtime_state(loaded_model=runtime_model_id, status="processing")

        try:
            self.logger.info(
                "transcribing",
                audio_path=str(audio_path),
                runtime_model_id=runtime_model_id,
                language=language,
            )

            # Build model-specific messages
            messages = adapter.build_messages(
                audio_path=audio_path,
                language=language,
            )

            # Build sampling parameters
            from vllm import SamplingParams

            sampling_kwargs = adapter.get_sampling_kwargs()
            sampling_params = SamplingParams(**sampling_kwargs)

            # Generate transcription
            outputs = self._llm.chat(
                messages=messages,
                sampling_params=sampling_params,
            )

            raw_text = outputs[0].outputs[0].text

            self.logger.info(
                "transcription_complete",
                char_count=len(raw_text),
            )

            # Parse output using adapter
            result = adapter.parse_output(raw_text, language)

            # Override engine_id and add channel/warnings
            result.engine_id = self._engine_id
            result.channel = channel
            result.warnings = warnings + (result.warnings or [])

            return TaskOutput(data=result)

        finally:
            self._set_runtime_state(loaded_model=runtime_model_id, status="idle")

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
            "engine_id": self._engine_id,
            "model_loaded": self._llm is not None,
            "loaded_model_id": self._loaded_model_id,
            "supported_models": sorted(ADAPTER_REGISTRY.keys()),
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
            "gpu_memory_utilization": self._gpu_memory_utilization,
        }

    def get_capabilities(self) -> EngineCapabilities:
        """Return vLLM-ASR engine capabilities."""
        return EngineCapabilities(
            engine_id=self._engine_id,
            version="1.0.0",
            stages=["transcribe"],
            languages=None,  # Multilingual (model-dependent)
            supports_word_timestamps=False,  # Audio LLMs don't produce timestamps
            supports_streaming=False,
            model_variants=sorted(ADAPTER_REGISTRY.keys()),
            gpu_required=True,
            gpu_vram_mb=8000,  # Minimum for smallest model
            supports_cpu=False,
            min_ram_gb=16,
            rtf_gpu=0.15,
            runtime="vllm-asr",
        )

    def shutdown(self) -> None:
        """Shutdown engine and release GPU resources."""
        self.logger.info("engine_shutdown")
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._loaded_model_id = None

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

        super().shutdown()


if __name__ == "__main__":
    engine = VLLMASREngine()
    engine.run()
