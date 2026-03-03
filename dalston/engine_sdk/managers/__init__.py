"""Framework-specific model managers for Dalston engines.

This package provides ModelManager implementations for different ML frameworks:

- FasterWhisperModelManager: CTranslate2/faster-whisper models
- HFTransformersModelManager: HuggingFace Transformers ASR pipelines
- NeMoModelManager: NVIDIA NeMo Parakeet ASR models (M44)
- NeMoOnnxModelManager: ONNX-optimized Parakeet models (M44)

Example usage:
    from dalston.engine_sdk.managers import FasterWhisperModelManager

    manager = FasterWhisperModelManager(
        device="cuda",
        compute_type="float16",
        ttl_seconds=3600,
        max_loaded=2,
    )

    model = manager.acquire("large-v3-turbo")
    try:
        segments, info = model.transcribe(audio_path)
    finally:
        manager.release("large-v3-turbo")

    # M44: NeMo model manager
    from dalston.engine_sdk.managers import NeMoModelManager

    nemo_manager = NeMoModelManager(device="cuda")
    model = nemo_manager.acquire("parakeet-rnnt-1.1b")
    try:
        hypotheses = model.transcribe([audio])
    finally:
        nemo_manager.release("parakeet-rnnt-1.1b")
"""

from dalston.engine_sdk.managers.faster_whisper import FasterWhisperModelManager
from dalston.engine_sdk.managers.hf_transformers import HFTransformersModelManager
from dalston.engine_sdk.managers.nemo import NeMoModelManager
from dalston.engine_sdk.managers.nemo_onnx import NeMoOnnxModelManager

__all__ = [
    "FasterWhisperModelManager",
    "HFTransformersModelManager",
    "NeMoModelManager",
    "NeMoOnnxModelManager",
]
