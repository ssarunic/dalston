"""HuggingFace Transformers model manager for ASR pipelines.

This manager handles loading and lifecycle management for HuggingFace
Transformers models using the automatic-speech-recognition pipeline.

Supports any model on HuggingFace Hub with pipeline_tag=automatic-speech-recognition,
including Whisper, Wav2Vec2, HuBERT, MMS, and community fine-tunes.

Example usage:
    # With multi-source storage (production):
    storage = MultiSourceModelStorage.from_env()
    manager = HFTransformersModelManager(
        device="cuda",
        torch_dtype=torch.float16,
        model_storage=storage,
        ttl_seconds=3600,
        max_loaded=2,
    )

    # Without storage (local development):
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
    DALSTON_MODEL_SOURCE: Model source ("s3", "hf", "auto"; default: "s3")
    DALSTON_S3_BUCKET: S3 bucket for models (used when source includes S3)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from dalston.engine_sdk.model_manager import ModelManager

if TYPE_CHECKING:
    import torch

    from dalston.engine_sdk.model_storage import MultiSourceModelStorage

logger = structlog.get_logger()


class HFTransformersModelManager(ModelManager[Any]):
    """Model manager for HuggingFace Transformers ASR pipelines.

    This manager handles the lifecycle of HuggingFace ASR pipelines, including:
    - Automatic model downloading via MultiSourceModelStorage
    - Device and dtype configuration
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    The returned object is a ``transformers.pipeline`` instance configured for
    ``automatic-speech-recognition``.

    Args:
        device: Device for inference ("cuda", "cpu", or device index)
        torch_dtype: PyTorch dtype for model weights
        model_storage: Optional MultiSourceModelStorage for model downloads
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    def __init__(
        self,
        device: str = "cuda",
        torch_dtype: torch.dtype | None = None,
        model_storage: MultiSourceModelStorage | None = None,
        **kwargs: Any,
    ) -> None:
        self.device = device
        self.torch_dtype = torch_dtype
        self.model_storage = model_storage

        logger.info(
            "hf_transformers_manager_init",
            device=self.device,
            torch_dtype=str(self.torch_dtype),
            storage_enabled=model_storage is not None,
        )

        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> Any:
        """Load a HuggingFace ASR pipeline.

        If MultiSourceModelStorage is configured, models are downloaded via
        the configured source. Otherwise, HuggingFace downloads directly.

        Args:
            model_id: HuggingFace model ID (e.g., "openai/whisper-large-v3")

        Returns:
            Loaded transformers pipeline instance
        """
        from transformers import pipeline

        # If storage is configured, download from configured source first
        model_path: str = model_id
        if self.model_storage is not None:
            logger.info(
                "ensuring_model_from_storage",
                model_id=model_id,
            )
            local_path = self.model_storage.ensure_local(model_id)
            model_path = str(local_path)
            logger.info(
                "model_ready_from_storage",
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

        pipe = pipeline(
            "automatic-speech-recognition",
            trust_remote_code=False,
            **pipe_kwargs,
        )

        logger.info(
            "hf_asr_pipeline_loaded",
            model_id=model_id,
            device=self.device,
        )

        return pipe

    def _unload_model(self, model: Any) -> None:
        """Unload a HuggingFace ASR pipeline."""
        del model

    def get_local_cache_stats(self) -> dict | None:
        """Get local model cache statistics.

        Returns:
            Dictionary with cache stats if storage is configured,
            None otherwise.
        """
        if self.model_storage is not None:
            return self.model_storage.get_cache_stats()
        return None

    @classmethod
    def from_env(cls) -> HFTransformersModelManager:
        """Create a manager configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
            DALSTON_MODEL_SOURCE: Source mode ("s3", "hf", "auto")
            DALSTON_S3_BUCKET: S3 bucket (used when source includes S3)
            DALSTON_MODEL_CACHE_MAX_GB: Max disk cache in GB (0 = unlimited)
            DALSTON_MODEL_CACHE_TTL_HOURS: Max hours since last access (0 = unlimited)

        Returns:
            Configured HFTransformersModelManager instance
        """
        import os

        from dalston.engine_sdk.device import detect_device
        from dalston.engine_sdk.disk_cache import start_disk_evictor
        from dalston.engine_sdk.model_storage import MultiSourceModelStorage

        device = detect_device(include_mps=False)

        torch_dtype = None
        if device == "cuda":
            import torch

            torch_dtype = torch.float16

        model_storage = MultiSourceModelStorage.from_env()

        manager = cls(
            device=device,
            torch_dtype=torch_dtype,
            model_storage=model_storage,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        manager._disk_evictor = start_disk_evictor(manager, model_storage)

        return manager
