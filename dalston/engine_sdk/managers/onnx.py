"""ONNX Runtime model manager for ONNX-exported ASR models.

This manager handles loading and lifecycle management for ONNX-exported ASR
models using the onnx-asr library. Much lighter than full NeMo (no PyTorch needed
for inference).

The manager supports any model that onnx_asr.load_model() accepts. A curated
set of short aliases (MODEL_ALIASES) is provided for convenience, but unknown
model IDs are passed through to onnx-asr as-is — enabling Whisper, GigaAM,
Vosk, NeMo Conformer/Canary, Kaldi, and arbitrary HuggingFace model paths.

Curated aliases:
    CTC:  parakeet-onnx-ctc-0.6b, parakeet-onnx-ctc-1.1b
    TDT:  parakeet-onnx-tdt-0.6b-v2, parakeet-onnx-tdt-0.6b-v3
    RNNT: parakeet-onnx-rnnt-0.6b

Example usage:
    from dalston.engine_sdk.managers import OnnxModelManager

    manager = OnnxModelManager(
        device="cpu",
        ttl_seconds=3600,
        max_loaded=2,
    )

    # Using a curated alias
    model = manager.acquire("parakeet-onnx-ctc-0.6b")

    # Or pass any onnx-asr compatible model ID directly
    model = manager.acquire("openai/whisper-large-v3")

    try:
        result = model.recognize(audio_array, sample_rate=16000)
    finally:
        manager.release("parakeet-onnx-ctc-0.6b")

Environment variables:
    DALSTON_MODEL_TTL_SECONDS: Default TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models to keep loaded (default: 2)
    DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Default: none.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import structlog

from dalston.engine_sdk.model_manager import ModelManager

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()


# Type alias for ONNX ASR models (to avoid import at module level)
OnnxASRModel = Any


class OnnxModelManager(ModelManager[OnnxASRModel]):
    """Model manager for ONNX Runtime ASR models.

    This manager handles the lifecycle of ONNX ASR models using onnx-asr:
    - Automatic model downloading from HuggingFace Hub
    - Support for different architectures (CTC, TDT, RNNT)
    - Device configuration (CUDA/CPU via ONNX Runtime providers)
    - Optional quantization (int8)
    - TTL-based eviction for idle models
    - LRU eviction when at capacity

    Args:
        device: Device for inference ("cuda" or "cpu")
        quantization: Quantization level ("none" or "int8")
        **kwargs: Passed to ModelManager (ttl_seconds, max_loaded, preload)
    """

    # Curated aliases: friendly model ID → onnx-asr model name.
    # Unknown IDs are passed through to onnx_asr.load_model() as-is,
    # so this is a convenience mapping, not a gatekeeper.
    MODEL_ALIASES: dict[str, str] = {
        # CTC models
        "parakeet-onnx-ctc-0.6b": "nemo-parakeet-ctc-0.6b",
        "parakeet-onnx-ctc-1.1b": "nemo-parakeet-ctc-1.1b",
        # TDT models
        "parakeet-onnx-tdt-0.6b-v2": "nemo-parakeet-tdt-0.6b-v2",
        "parakeet-onnx-tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3",
        # RNNT models
        "parakeet-onnx-rnnt-0.6b": "nemo-parakeet-rnnt-0.6b",
        # Short aliases (without parakeet-onnx- prefix)
        "ctc-0.6b": "nemo-parakeet-ctc-0.6b",
        "ctc-1.1b": "nemo-parakeet-ctc-1.1b",
        "tdt-0.6b-v2": "nemo-parakeet-tdt-0.6b-v2",
        "tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3",
        "rnnt-0.6b": "nemo-parakeet-rnnt-0.6b",
    }

    def __init__(
        self,
        device: str = "cpu",
        quantization: str = "none",
        **kwargs,
    ) -> None:
        self.device = device
        self.quantization = quantization if quantization != "none" else None

        # Configure ONNX Runtime providers based on device
        self._providers: list[str | tuple[str, dict]] = []
        if device == "cuda":
            self._providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            self._providers = ["CPUExecutionProvider"]

        logger.info(
            "onnx_model_manager_init",
            device=self.device,
            quantization=self.quantization,
            providers=self._providers,
        )

        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> OnnxASRModel:
        """Load an ONNX ASR model.

        Args:
            model_id: Model identifier. Can be a curated alias
                (e.g., "parakeet-onnx-ctc-0.6b", "ctc-0.6b") or any model ID
                accepted by onnx_asr.load_model() (e.g., "openai/whisper-large-v3").

        Returns:
            Loaded onnx-asr model instance

        Raises:
            ImportError: If onnx-asr is not installed
            Exception: If model loading fails
        """
        # Resolve alias if known, otherwise pass through as-is
        onnx_asr_name = self.MODEL_ALIASES.get(model_id, model_id)

        # Import onnx-asr (deferred to avoid import errors if not installed)
        try:
            import onnx_asr
        except ImportError as e:
            raise ImportError(
                "onnx-asr not installed. Install with: pip install onnx-asr[cpu,hub]"
            ) from e

        logger.info(
            "loading_onnx_model",
            model_id=model_id,
            onnx_asr_name=onnx_asr_name,
            device=self.device,
            quantization=self.quantization,
        )

        # Load model with onnx-asr
        kwargs: dict[str, Any] = {}
        if self._providers:
            kwargs["providers"] = self._providers

        model = onnx_asr.load_model(
            onnx_asr_name,
            quantization=self.quantization,
            **kwargs,
        )

        logger.info(
            "onnx_model_loaded",
            model_id=model_id,
            device=self.device,
        )

        return model

    def _unload_model(self, model: OnnxASRModel) -> None:
        """Unload an ONNX ASR model.

        Args:
            model: The ONNX model to unload
        """
        # ONNX models don't have explicit cleanup, just delete reference
        del model

    def _cleanup_gpu_memory(self) -> None:
        """Clean up GPU memory after model unload.

        For ONNX Runtime, we try to clear CUDA memory if using CUDA provider.
        """
        if self.device == "cuda":
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    @classmethod
    def from_env(cls) -> OnnxModelManager:
        """Create a manager configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_QUANTIZATION: Quantization level ("none" or "int8", default: none)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)

        Returns:
            Configured OnnxModelManager instance
        """
        # Auto-detect device
        device = os.environ.get("DALSTON_DEVICE", "").lower()
        if not device or device == "auto":
            try:
                import onnxruntime as ort

                if "CUDAExecutionProvider" in ort.get_available_providers():
                    device = "cuda"
                else:
                    device = "cpu"
            except ImportError:
                device = "cpu"

        quantization = os.environ.get("DALSTON_QUANTIZATION", "none").lower()

        return cls(
            device=device,
            quantization=quantization,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )
