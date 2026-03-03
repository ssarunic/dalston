"""Framework-specific model managers for Dalston engines.

This package provides ModelManager implementations for different ML frameworks:

- FasterWhisperModelManager: CTranslate2/faster-whisper models
- HFTransformersModelManager: HuggingFace Transformers ASR pipelines
- (Future) NeMoModelManager: NVIDIA NeMo checkpoints

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
"""

from dalston.engine_sdk.managers.faster_whisper import FasterWhisperModelManager
from dalston.engine_sdk.managers.hf_transformers import HFTransformersModelManager

__all__ = [
    "FasterWhisperModelManager",
    "HFTransformersModelManager",
]
