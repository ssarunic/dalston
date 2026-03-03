"""HuggingFace Transformers model manager for ASR pipelines.

This manager handles loading and lifecycle management for HuggingFace
Transformers models using the automatic-speech-recognition pipeline.

Supports any model on HuggingFace Hub with pipeline_tag=automatic-speech-recognition,
including Whisper, Wav2Vec2, HuBERT, MMS, and community fine-tunes.

Example usage:
    # With S3 storage (production):
    storage = S3ModelStorage.from_env()
    manager = HFTransformersModelManager(
        device="cuda",
        torch_dtype=torch.float16,
        model_storage=storage,
        ttl_seconds=3600,
        max_loaded=2,
    )

    # Without S3 (local development):
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
    DALSTON_S3_BUCKET: S3 bucket for models (enables S3 storage)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from dalston.engine_sdk.model_manager import ModelManager

if TYPE_CHECKING:
    import torch

    from dalston.engine_sdk.model_storage import S3ModelStorage

logger = structlog.get_logger()


class HFTransformersModelManager(ModelManager[Any]):
    """Model manager for HuggingFace Transformers ASR pipelines.

    This manager handles the lifecycle of HuggingFace ASR pipelines, including:
    - Automatic model downloading from HuggingFace Hub (or S3 if configured)
    - Device and dtype configuration
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    The returned object is a ``transformers.pipeline`` instance configured for
    ``automatic-speech-recognition``.

    Args:
        device: Device for inference ("cuda", "cpu", or device index)
        torch_dtype: PyTorch dtype for model weights
        model_storage: Optional S3ModelStorage for model caching
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    def __init__(
        self,
        device: str = "cuda",
        torch_dtype: torch.dtype | None = None,
        model_storage: S3ModelStorage | None = None,
        **kwargs: Any,
    ) -> None:
        self.device = device
        self.torch_dtype = torch_dtype
        self.model_storage = model_storage

        logger.info(
            "hf_transformers_manager_init",
            device=self.device,
            torch_dtype=str(self.torch_dtype),
            s3_storage_enabled=model_storage is not None,
        )

        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> Any:
        """Load a HuggingFace ASR pipeline.

        If S3ModelStorage is configured, models are downloaded from S3.
        Otherwise, models are downloaded from HuggingFace Hub directly.

        Args:
            model_id: HuggingFace model ID (e.g., "openai/whisper-large-v3")

        Returns:
            Loaded transformers pipeline instance

        Raises:
            ModelNotInS3Error: If S3 storage is enabled but model is not in S3
            Exception: If model loading fails
        """
        from transformers import pipeline

        # If S3 storage is configured, download from S3 first
        model_path: str = model_id
        if self.model_storage is not None:
            logger.info(
                "ensuring_model_from_s3",
                model_id=model_id,
            )
            local_path = self.model_storage.ensure_local(model_id)
            model_path = str(local_path)
            logger.info(
                "model_ready_from_s3",
                model_id=model_id,
                local_path=model_path,
            )

        logger.info(
            "loading_hf_asr_pipeline",
            model_id=model_id,
            model_path=model_path,
            device=self.device,
            torch_dtype=str(self.torch_dtype),
        )

        # Build pipeline kwargs
        pipe_kwargs: dict[str, Any] = {
            "model": model_path,
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

    def get_local_cache_stats(self) -> dict | None:
        """Get local model cache statistics from S3ModelStorage.

        Returns:
            Dictionary with cache stats if S3 storage is configured,
            None otherwise.
        """
        if self.model_storage is not None:
            return self.model_storage.get_cache_stats()
        return None
