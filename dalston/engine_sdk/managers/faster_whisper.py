"""Faster Whisper model manager for CTranslate2-based Whisper models.

This manager handles loading and lifecycle management for faster-whisper models,
which are CTranslate2 conversions of OpenAI Whisper models.

Accepts any HuggingFace model ID (e.g., "Systran/faster-whisper-large-v3")
or short names that faster-whisper's WhisperModel constructor understands
(e.g., "large-v3-turbo").

Example usage:
    # With multi-source storage (production):
    storage = MultiSourceModelStorage.from_env()
    manager = FasterWhisperModelManager(
        device="cuda",
        compute_type="float16",
        model_storage=storage,
    )

    # Without storage (local development, downloads via faster-whisper):
    manager = FasterWhisperModelManager.from_env()

    model = manager.acquire("large-v3-turbo")
    try:
        segments, info = model.transcribe("audio.wav", language="en")
    finally:
        manager.release("large-v3-turbo")

Environment variables:
    WHISPER_MODELS_DIR: Directory for model cache (default: from model_paths)
    DALSTON_MODEL_TTL_SECONDS: Default TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models to keep loaded (default: 2)
    DALSTON_MODEL_SOURCE: Model source ("s3", "hf", "auto"; default: "s3")
    DALSTON_S3_BUCKET: S3 bucket for models (used when source includes S3)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog

from dalston.engine_sdk.model_manager import ModelManager
from dalston.engine_sdk.model_paths import CTRANSLATE2_CACHE

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

    from dalston.engine_sdk.model_storage import MultiSourceModelStorage

logger = structlog.get_logger()


def _is_missing_cuda_shared_library_error(exc: Exception) -> bool:
    """Detect CTranslate2 CUDA shared-library load failures."""
    lowered = str(exc).lower()
    if "is not found or cannot be loaded" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "libcublas",
            "libcudnn",
            "libcuda",
            "libnvrtc",
        )
    )


class FasterWhisperModelManager(ModelManager["WhisperModel"]):
    """Model manager for CTranslate2/faster-whisper models.

    This manager handles the lifecycle of Whisper models, including:
    - Automatic model downloading via MultiSourceModelStorage
    - Device and compute type configuration
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    Args:
        device: Device for inference ("cuda" or "cpu")
        compute_type: Compute precision ("float16", "int8", "float32")
        download_root: Directory to cache downloaded models
        model_storage: MultiSourceModelStorage for model downloads
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    def __init__(
        self,
        device: str = "cuda",
        compute_type: str = "float16",
        download_root: str | None = None,
        model_storage: MultiSourceModelStorage | None = None,
        **kwargs,
    ) -> None:
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root or str(CTRANSLATE2_CACHE / "faster-whisper")
        self.model_storage = model_storage

        # Override from environment if set
        env_download_root = os.environ.get("WHISPER_MODELS_DIR")
        if env_download_root:
            self.download_root = env_download_root

        logger.info(
            "faster_whisper_manager_init",
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
            storage_enabled=model_storage is not None,
        )

        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> WhisperModel:
        """Load a faster-whisper model.

        If MultiSourceModelStorage is configured, models are downloaded via
        the configured source (S3, HF, or auto). Otherwise, models are
        downloaded by faster-whisper directly (uses HF Hub internally).

        Args:
            model_id: Model identifier — full HF repo ID
                (e.g., "Systran/faster-whisper-large-v3") or short name
                that faster-whisper accepts (e.g., "large-v3-turbo")

        Returns:
            Loaded WhisperModel instance
        """
        # Import here to avoid import errors if faster-whisper not installed
        from faster_whisper import WhisperModel

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
            "loading_faster_whisper_model",
            model_id=model_id,
            model_path=model_path,
            device=self.device,
            compute_type=self.compute_type,
        )

        try:
            model = WhisperModel(
                model_path,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.download_root,
            )
        except Exception as e:
            if self.device == "cuda" and _is_missing_cuda_shared_library_error(e):
                raise RuntimeError(
                    "faster-whisper failed to initialize on CUDA because required "
                    f"CUDA shared libraries are unavailable ({e}). "
                    "Rebuild the GPU image so CUDA 12 runtime libraries are present "
                    "or set DALSTON_DEVICE=cpu."
                ) from e
            raise

        logger.info(
            "faster_whisper_model_loaded",
            model_id=model_id,
            device=self.device,
        )

        return model

    def _unload_model(self, model: WhisperModel) -> None:
        """Unload a faster-whisper model."""
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
    def from_env(cls) -> FasterWhisperModelManager:
        """Create a manager configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
            WHISPER_MODELS_DIR: Download directory (optional)
            DALSTON_MODEL_SOURCE: Source mode ("s3", "hf", "auto")
            DALSTON_S3_BUCKET: S3 bucket (used when source includes S3)
            DALSTON_MODEL_CACHE_MAX_GB: Max disk cache in GB (0 = unlimited)
            DALSTON_MODEL_CACHE_TTL_HOURS: Max hours since last access (0 = unlimited)

        Returns:
            Configured FasterWhisperModelManager instance
        """
        from dalston.engine_sdk.device import detect_device
        from dalston.engine_sdk.model_storage import MultiSourceModelStorage

        device = detect_device(include_mps=False)
        compute_type = "float16" if device == "cuda" else "int8"

        model_storage = MultiSourceModelStorage.from_env()

        manager = cls(
            device=device,
            compute_type=compute_type,
            model_storage=model_storage,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        # Start disk cache evictor if limits are configured
        from dalston.engine_sdk.disk_cache import start_disk_evictor

        manager._disk_evictor = start_disk_evictor(manager, model_storage)

        return manager
