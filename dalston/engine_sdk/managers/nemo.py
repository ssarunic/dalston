"""NeMo model manager for NVIDIA Parakeet ASR models.

This manager handles loading and lifecycle management for NeMo ASR models
with support for RNNT, CTC, and TDT architectures.

Supported models:
    RNNT (offline; buffered streaming via BatchedFrameASRRNNT):
    - parakeet-rnnt-0.6b: nvidia/parakeet-rnnt-0.6b
    - parakeet-rnnt-1.1b: nvidia/parakeet-rnnt-1.1b
    - nemotron-streaming-rnnt-0.6b: nvidia/nemotron-speech-streaming-en-0.6b (cache-aware)

    CTC (non-streaming):
    - parakeet-ctc-0.6b: nvidia/parakeet-ctc-0.6b
    - parakeet-ctc-1.1b: nvidia/parakeet-ctc-1.1b

    TDT (offline; buffered streaming via BatchedFrameASRRNNT):
    - parakeet-tdt-0.6b-v3: nvidia/parakeet-tdt-0.6b-v3
    - parakeet-tdt-1.1b: nvidia/parakeet-tdt-1.1b

Example usage:
    from dalston.engine_sdk.managers import NeMoModelManager

    manager = NeMoModelManager(
        device="cuda",
        ttl_seconds=3600,
        max_loaded=2,
    )

    model = manager.acquire("parakeet-rnnt-1.1b")
    try:
        hypotheses = model.transcribe([audio_array], return_hypotheses=True)
    finally:
        manager.release("parakeet-rnnt-1.1b")

Environment variables:
    NEMO_CACHE: Directory for NeMo model cache (default: /models/nemo)
    DALSTON_MODEL_TTL_SECONDS: Default TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models to keep loaded (default: 2)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import structlog

from dalston.engine_sdk.model_manager import ModelManager

if TYPE_CHECKING:
    from dalston.engine_sdk.model_storage import MultiSourceModelStorage

logger = structlog.get_logger()


# Type alias for NeMo ASR models (to avoid import at module level)
NeMoASRModel = Any


class NeMoModelManager(ModelManager[NeMoASRModel]):
    """Model manager for NVIDIA NeMo Parakeet ASR models.

    This manager handles the lifecycle of NeMo ASR models, including:
    - Automatic model downloading from NGC/HuggingFace
    - Support for different architectures (RNNT, CTC, TDT)
    - Device configuration (CUDA/CPU)
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    Args:
        device: Device for inference ("cuda" or "cpu")
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    # Models trained with limited right context — support per-chunk BatchedFrameASRRNNT streaming.
    # Offline RNNT/TDT (parakeet-*) require full audio and are NOT listed here.
    CACHE_AWARE_STREAMING_MODELS = frozenset({"nemotron-streaming-rnnt-0.6b"})

    # Model ID to NGC/HuggingFace model path mapping
    SUPPORTED_MODELS = {
        # RNNT models (offline; streaming uses BatchedFrameASRRNNT buffered inference)
        "parakeet-rnnt-0.6b": "nvidia/parakeet-rnnt-0.6b",
        "parakeet-rnnt-1.1b": "nvidia/parakeet-rnnt-1.1b",
        # Nemotron streaming model — purpose-built for cache-aware streaming RNNT
        # (released Jan 2026; supports 80/160/560/1120ms latency tiers)
        "nemotron-streaming-rnnt-0.6b": "nvidia/nemotron-speech-streaming-en-0.6b",
        # CTC models (non-streaming; requires full sequence for decode)
        "parakeet-ctc-0.6b": "nvidia/parakeet-ctc-0.6b",
        "parakeet-ctc-1.1b": "nvidia/parakeet-ctc-1.1b",
        # TDT models (offline; streaming uses BatchedFrameASRRNNT buffered inference)
        "parakeet-tdt-0.6b-v3": "nvidia/parakeet-tdt-0.6b-v3",
        "parakeet-tdt-1.1b": "nvidia/parakeet-tdt-1.1b",
    }

    # Architecture to NeMo model class mapping
    # TDT uses the RNNT base class
    ARCHITECTURE_LOADERS = {
        "rnnt": "EncDecRNNTBPEModel",
        "ctc": "EncDecCTCModelBPE",
        "tdt": "EncDecRNNTBPEModel",  # TDT uses RNNT base
    }

    def __init__(
        self,
        device: str = "cuda",
        model_storage: MultiSourceModelStorage | None = None,
        **kwargs,
    ) -> None:
        self.device = device
        self.model_storage = model_storage

        # NeMo cache directory
        self.nemo_cache = os.environ.get("NEMO_CACHE", "/models/nemo")

        logger.info(
            "nemo_model_manager_init",
            device=self.device,
            nemo_cache=self.nemo_cache,
            storage_enabled=model_storage is not None,
        )

        super().__init__(**kwargs)

    def is_cache_aware_streaming(self, model_id: str) -> bool:
        """Return True if model was trained for per-chunk cache-aware streaming.

        Only models with limited right context (e.g. nemotron-speech-streaming-en-0.6b)
        return True. Offline models (parakeet-rnnt-*, parakeet-tdt-*) return False even
        though they share the RNNT/TDT architecture.
        """
        return model_id in self.CACHE_AWARE_STREAMING_MODELS

    def get_architecture(self, model_id: str) -> str:
        """Determine architecture from model ID.

        Args:
            model_id: Model identifier (e.g., "parakeet-rnnt-1.1b")

        Returns:
            Architecture string ("rnnt", "ctc", or "tdt")
        """
        model_id_lower = model_id.lower()
        if "rnnt" in model_id_lower:
            return "rnnt"
        if "ctc" in model_id_lower:
            return "ctc"
        if "tdt" in model_id_lower:
            return "tdt"

        # Default to RNNT for unknown architectures
        logger.warning(
            "unknown_architecture_defaulting_to_rnnt",
            model_id=model_id,
        )
        return "rnnt"

    def _load_model(self, model_id: str) -> NeMoASRModel:
        """Load a NeMo ASR model.

        Args:
            model_id: Model identifier (e.g., "parakeet-rnnt-1.1b")

        Returns:
            Loaded NeMo ASRModel instance

        Raises:
            ValueError: If model_id is not supported
            ImportError: If NeMo is not installed
            Exception: If model loading fails
        """
        # Resolve model path
        if model_id in self.SUPPORTED_MODELS:
            model_path = self.SUPPORTED_MODELS[model_id]
        elif "/" in model_id:
            # Allow HuggingFace model IDs (contain "/")
            model_path = model_id
        else:
            raise ValueError(
                f"Unknown model: {model_id}. "
                f"Supported: {sorted(self.SUPPORTED_MODELS.keys())} or HuggingFace model IDs"
            )

        # Use Dalston model storage if configured (S3 → HF fallback).
        # If storage doesn't have the model (e.g. NGC-only models where the
        # NGC backend is not yet implemented), fall through to NeMo's built-in
        # NGC/HF downloader.
        if self.model_storage is not None:
            from dalston.engine_sdk.model_storage import ModelNotFoundError

            try:
                logger.info("ensuring_model_from_storage", model_id=model_id)
                local_path = self.model_storage.ensure_local(model_path)
                model_path = str(local_path)
                logger.info(
                    "model_ready_from_storage",
                    model_id=model_id,
                    local_path=model_path,
                )
            except ModelNotFoundError:
                logger.info(
                    "model_not_in_storage_using_nemo_downloader",
                    model_id=model_id,
                    model_path=model_path,
                )

        # Import NeMo (deferred to avoid import errors if not installed)
        try:
            import nemo.collections.asr as nemo_asr
        except ImportError as e:
            raise ImportError(
                "NeMo toolkit not installed. Install with: pip install nemo_toolkit[asr]"
            ) from e

        # Determine architecture and get appropriate loader
        architecture = self.get_architecture(model_id)
        loader_name = self.ARCHITECTURE_LOADERS[architecture]
        loader = getattr(nemo_asr.models, loader_name)

        logger.info(
            "loading_nemo_model",
            model_id=model_id,
            model_path=model_path,
            architecture=architecture,
            device=self.device,
        )

        model = loader.from_pretrained(model_path)

        # Move to device and set to eval mode
        model = model.to(self.device)
        model.eval()

        logger.info(
            "nemo_model_loaded",
            model_id=model_id,
            device=self.device,
        )

        return model

    def _unload_model(self, model: NeMoASRModel) -> None:
        """Unload a NeMo ASR model.

        Args:
            model: The NeMo model to unload
        """
        # NeMo models don't have explicit cleanup, just delete reference
        del model

    def _cleanup_gpu_memory(self) -> None:
        """Clean up GPU memory after model unload."""
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def get_local_cache_stats(self) -> dict | None:
        """Get local model cache statistics."""
        if self.model_storage is not None:
            return self.model_storage.get_cache_stats()
        return None

    @classmethod
    def from_env(cls) -> NeMoModelManager:
        """Create a manager configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
            NEMO_CACHE: NeMo cache directory (optional)

        Returns:
            Configured NeMoModelManager instance
        """
        from dalston.engine_sdk.device import detect_device
        from dalston.engine_sdk.disk_cache import start_disk_evictor
        from dalston.engine_sdk.model_storage import MultiSourceModelStorage

        device = detect_device(include_mps=False)
        model_storage = MultiSourceModelStorage.from_env()

        manager = cls(
            device=device,
            model_storage=model_storage,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        manager._disk_evictor = start_disk_evictor(manager, model_storage)

        return manager
