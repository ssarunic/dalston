"""Faster Whisper model manager for CTranslate2-based Whisper models.

This manager handles loading and lifecycle management for faster-whisper models,
which are CTranslate2 conversions of OpenAI Whisper models.

Supported models:
    - tiny, base, small, medium (lightweight models)
    - large-v2, large-v3 (full accuracy)
    - large-v3-turbo (accuracy + speed, CPU-capable)
    - distil-large-v3 (distilled, faster)

Example usage:
    manager = FasterWhisperModelManager(
        device="cuda",
        compute_type="float16",
        ttl_seconds=3600,
        max_loaded=2,
        preload="large-v3-turbo",
    )

    model = manager.acquire("large-v3-turbo")
    try:
        segments, info = model.transcribe("audio.wav", language="en")
    finally:
        manager.release("large-v3-turbo")

Environment variables:
    WHISPER_MODELS_DIR: Directory for model cache (default: from model_paths)
    DALSTON_MODEL_TTL_SECONDS: Default TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models to keep loaded (default: 2)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog

from dalston.engine_sdk.model_manager import ModelManager
from dalston.engine_sdk.model_paths import CTRANSLATE2_CACHE

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = structlog.get_logger()


class FasterWhisperModelManager(ModelManager["WhisperModel"]):
    """Model manager for CTranslate2/faster-whisper models.

    This manager handles the lifecycle of Whisper models, including:
    - Automatic model downloading from HuggingFace Hub
    - Device and compute type configuration
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    Args:
        device: Device for inference ("cuda" or "cpu")
        compute_type: Compute precision ("float16", "int8", "float32")
        download_root: Directory to cache downloaded models
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    # Valid model identifiers that can be loaded
    SUPPORTED_MODELS = frozenset(
        {
            "tiny",
            "tiny.en",
            "base",
            "base.en",
            "small",
            "small.en",
            "medium",
            "medium.en",
            "large-v1",
            "large-v2",
            "large-v3",
            "large-v3-turbo",
            "distil-large-v2",
            "distil-large-v3",
            "distil-medium.en",
            "distil-small.en",
        }
    )

    def __init__(
        self,
        device: str = "cuda",
        compute_type: str = "float16",
        download_root: str | None = None,
        **kwargs,
    ) -> None:
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root or str(CTRANSLATE2_CACHE / "faster-whisper")

        # Override from environment if set
        env_download_root = os.environ.get("WHISPER_MODELS_DIR")
        if env_download_root:
            self.download_root = env_download_root

        logger.info(
            "faster_whisper_manager_init",
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> WhisperModel:
        """Load a faster-whisper model.

        Args:
            model_id: Model identifier (e.g., "large-v3-turbo")

        Returns:
            Loaded WhisperModel instance

        Raises:
            ValueError: If model_id is not supported
            Exception: If model loading fails
        """
        # Import here to avoid import errors if faster-whisper not installed
        from faster_whisper import WhisperModel

        # Validate model ID
        if model_id not in self.SUPPORTED_MODELS:
            # Allow HuggingFace model IDs (contain "/")
            if "/" not in model_id:
                raise ValueError(
                    f"Unknown model: {model_id}. "
                    f"Supported: {sorted(self.SUPPORTED_MODELS)} or HuggingFace model IDs"
                )

        logger.info(
            "loading_faster_whisper_model",
            model_id=model_id,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

        model = WhisperModel(
            model_id,
            device=self.device,
            compute_type=self.compute_type,
            download_root=self.download_root,
        )

        logger.info(
            "faster_whisper_model_loaded",
            model_id=model_id,
            device=self.device,
        )

        return model

    def _unload_model(self, model: WhisperModel) -> None:
        """Unload a faster-whisper model.

        Args:
            model: The WhisperModel to unload
        """
        # WhisperModel doesn't have explicit cleanup, just delete reference
        del model

    @classmethod
    def from_env(cls) -> FasterWhisperModelManager:
        """Create a manager configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
            WHISPER_MODELS_DIR: Download directory (optional)

        Returns:
            Configured FasterWhisperModelManager instance
        """
        # Auto-detect device
        device = os.environ.get("DALSTON_DEVICE", "").lower()
        if not device or device == "auto":
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        # Compute type based on device
        compute_type = "float16" if device == "cuda" else "int8"

        return cls(
            device=device,
            compute_type=compute_type,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )
