"""HuggingFace Transformers model manager for ASR pipelines.

This manager handles loading and lifecycle management for HuggingFace
Transformers models using the automatic-speech-recognition pipeline.

Supports any model on HuggingFace Hub with pipeline_tag=automatic-speech-recognition,
including Whisper, Wav2Vec2, HuBERT, MMS, and community fine-tunes.

Example usage:
    manager = HFTransformersModelManager(
        device="cuda",
        torch_dtype=torch.float16,
        ttl_seconds=3600,
        max_loaded=2,
        preload="openai/whisper-large-v3",
    )

    pipe = manager.acquire("openai/whisper-large-v3")
    try:
        result = pipe("audio.wav", return_timestamps="word")
    finally:
        manager.release("openai/whisper-large-v3")

Environment variables:
    DALSTON_MODEL_TTL_SECONDS: Default TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models to keep loaded (default: 2)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from dalston.engine_sdk.model_manager import ModelManager

if TYPE_CHECKING:
    import torch

logger = structlog.get_logger()


class HFTransformersModelManager(ModelManager[Any]):
    """Model manager for HuggingFace Transformers ASR pipelines.

    This manager handles the lifecycle of HuggingFace ASR pipelines, including:
    - Automatic model downloading from HuggingFace Hub
    - Device and dtype configuration
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    The returned object is a ``transformers.pipeline`` instance configured for
    ``automatic-speech-recognition``.

    Args:
        device: Device for inference ("cuda", "cpu", or device index)
        torch_dtype: PyTorch dtype for model weights
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    def __init__(
        self,
        device: str = "cuda",
        torch_dtype: torch.dtype | None = None,
        **kwargs: Any,
    ) -> None:
        self.device = device
        self.torch_dtype = torch_dtype
        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> Any:
        """Load a HuggingFace ASR pipeline.

        Args:
            model_id: HuggingFace model ID (e.g., "openai/whisper-large-v3")

        Returns:
            Loaded transformers pipeline instance

        Raises:
            Exception: If model loading fails
        """
        from transformers import pipeline

        logger.info(
            "loading_hf_asr_pipeline",
            model_id=model_id,
            device=self.device,
            torch_dtype=str(self.torch_dtype),
        )

        # Build pipeline kwargs
        pipe_kwargs: dict[str, Any] = {
            "model": model_id,
        }

        if self.device == "cpu":
            pipe_kwargs["device"] = "cpu"
        else:
            pipe_kwargs["device"] = self.device

        if self.torch_dtype is not None:
            pipe_kwargs["torch_dtype"] = self.torch_dtype

        pipe = pipeline("automatic-speech-recognition", **pipe_kwargs)

        logger.info(
            "hf_asr_pipeline_loaded",
            model_id=model_id,
            device=self.device,
        )

        return pipe

    def _unload_model(self, model: Any) -> None:
        """Unload a HuggingFace ASR pipeline.

        Args:
            model: The pipeline to unload
        """
        del model
