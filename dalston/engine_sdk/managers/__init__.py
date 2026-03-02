"""Framework-specific model managers for Dalston engines.

This package provides ModelManager implementations for different ML frameworks:

- FasterWhisperModelManager: CTranslate2/faster-whisper models
- (Future) NeMoModelManager: NVIDIA NeMo checkpoints
- (Future) HFTransformersModelManager: HuggingFace Transformers pipelines

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

__all__ = [
    "FasterWhisperModelManager",
]
