"""Real-time vLLM-ASR transcription engine.

Uses vLLM to serve audio-capable LLMs (Voxtral, Qwen2-Audio) for
real-time transcription of VAD-segmented utterances. Delegates inference
to dalston.vllm_asr shared helpers.

When run standalone, creates its own vLLM LLM instance in load_models().
When used within a unified runner, accepts an injected LLM to share a
single loaded model with the batch adapter.

Environment variables:
    DALSTON_ENGINE_ID: Engine ID for registration (default: "vllm-asr")
    DALSTON_DEFAULT_MODEL_ID: Default HF model ID
    DALSTON_VLLM_GPU_MEMORY_UTILIZATION: GPU memory fraction (default: 0.9)
    DALSTON_VLLM_MAX_MODEL_LEN: Maximum context length (default: 4096)
"""

from __future__ import annotations

import gc
import os
from typing import Any

import numpy as np
import structlog
import torch

from dalston.common.pipeline_types import Transcript, TranscriptionRequest
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine
from dalston.vllm_asr.inference import transcribe_audio_array

logger = structlog.get_logger()


class VllmAsrRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time transcription using audio-capable LLMs on vLLM.

    Supports any vLLM-compatible audio model (Voxtral, Qwen2-Audio, etc.).
    """

    DEFAULT_MODEL_ID = "mistralai/Voxtral-Mini-3B-2507"

    def __init__(self, llm: Any = None) -> None:
        """Initialize the engine.

        Args:
            llm: Optional shared vLLM LLM instance. If provided,
                 load_models() skips creating its own instance.
        """
        super().__init__()

        self._llm = llm
        self._loaded_model_id: str | None = None

        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "vllm-asr")
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )

        self._gpu_memory_utilization = float(
            os.environ.get("DALSTON_VLLM_GPU_MEMORY_UTILIZATION", "0.9")
        )
        self._max_model_len = int(os.environ.get("DALSTON_VLLM_MAX_MODEL_LEN", "4096"))

        logger.info(
            "vllm_asr_rt_engine_init",
            engine_id=self._engine_id,
            default_model_id=self._default_model_id,
            shared_llm=llm is not None,
        )

    def _ensure_model_loaded(self, model_id: str) -> None:
        """Ensure the requested model is loaded in vLLM."""
        if model_id == self._loaded_model_id and self._llm is not None:
            return

        if self._llm is not None:
            logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=model_id,
            )
            del self._llm
            self._llm = None
            self._loaded_model_id = None

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

        logger.info("loading_vllm_model", model_id=model_id)
        try:
            from vllm import LLM
        except ImportError as e:
            raise RuntimeError(
                "vLLM not installed. Install with: pip install 'vllm[audio]>=0.6.0'"
            ) from e

        self._llm = LLM(
            model=model_id,
            gpu_memory_utilization=self._gpu_memory_utilization,
            max_model_len=self._max_model_len,
            limit_mm_per_prompt={"audio": 1},
        )
        self._loaded_model_id = model_id
        logger.info("model_loaded", model_id=model_id)

    def load_models(self) -> None:
        """Load default model on startup."""
        if self._llm is None:
            self._ensure_model_loaded(self._default_model_id)
        else:
            # Shared LLM injected by unified runner
            self._loaded_model_id = self._default_model_id
            logger.info(
                "using_shared_llm",
                model_id=self._default_model_id,
            )

    def transcribe_v1(
        self, audio: np.ndarray, params: TranscriptionRequest
    ) -> Transcript:
        """Transcribe one audio window using vLLM."""
        model_id = params.loaded_model_id or self._default_model_id
        self._ensure_model_loaded(model_id)

        language = params.language
        if language == "" or language == "auto":
            language = None

        vocabulary = params.vocabulary or None
        if vocabulary:
            logger.debug(
                "vocabulary_via_instruction",
                terms_count=len(vocabulary),
                model_id=model_id,
            )

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        raw_text, transcript = transcribe_audio_array(
            llm=self._llm,
            audio=audio,
            language=language,
            sample_rate=16000,
            vocabulary=vocabulary,
        )

        transcript.engine_id = self._engine_id
        transcript.channel = params.channel

        return transcript

    def supports_native_streaming(self) -> bool:
        return True

    def get_models(self) -> list[str]:
        return []

    def get_engine_id(self) -> str:
        return self._engine_id

    def get_vocabulary_support(self):
        """vLLM-ASR supports vocabulary via instruction prompting."""
        from dalston.common.pipeline_types import VocabularyMethod, VocabularySupport

        return VocabularySupport(
            method=VocabularyMethod.INSTRUCTION,
            batch=True,
            realtime=True,
        )

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

        return {
            **base_health,
            "model_loaded": self._llm is not None,
            "loaded_model_id": self._loaded_model_id,
            "engine_id": self._engine_id,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }

    def shutdown(self) -> None:
        logger.info("vllm_asr_rt_shutdown")
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._loaded_model_id = None

            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            gc.collect()

        super().shutdown()


if __name__ == "__main__":
    import asyncio

    engine = VllmAsrRealtimeEngine()
    asyncio.run(engine.run())
