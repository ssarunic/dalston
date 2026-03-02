"""Model-specific adapters for vLLM audio LLMs.

Each adapter handles prompt building and output parsing for a specific
model family (Voxtral, Qwen2-Audio, etc.).
"""

from .base import AudioLLMAdapter
from .qwen2_audio import Qwen2AudioAdapter
from .voxtral import VoxtralAdapter

# Model HuggingFace ID → adapter class mapping
ADAPTER_REGISTRY: dict[str, type[AudioLLMAdapter]] = {
    "mistralai/Voxtral-Mini-3B-2507": VoxtralAdapter,
    "mistralai/Voxtral-Small-24B-2507": VoxtralAdapter,
    "Qwen/Qwen2-Audio-7B-Instruct": Qwen2AudioAdapter,
}


def get_adapter(model_id: str) -> AudioLLMAdapter:
    """Get the appropriate adapter for a model.

    Args:
        model_id: HuggingFace model identifier

    Returns:
        Instantiated adapter for the model

    Raises:
        ValueError: If no adapter exists for the model
    """
    adapter_cls = ADAPTER_REGISTRY.get(model_id)
    if adapter_cls is None:
        raise ValueError(
            f"No adapter for model: {model_id}. "
            f"Supported models: {sorted(ADAPTER_REGISTRY.keys())}"
        )
    return adapter_cls()


__all__ = [
    "ADAPTER_REGISTRY",
    "AudioLLMAdapter",
    "Qwen2AudioAdapter",
    "VoxtralAdapter",
    "get_adapter",
]
