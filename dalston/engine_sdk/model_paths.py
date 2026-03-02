"""Standardized model paths for all Dalston engines.

This module provides centralized path utilities for managing model weights
across different frameworks (HuggingFace, CTranslate2, NeMo, etc.).

All engines should use these utilities to ensure consistent cache structure:
- {base}/huggingface/  - HuggingFace Hub cache (transformers, diffusers)
- {base}/ctranslate2/  - CTranslate2 converted models (faster-whisper)
- {base}/nemo/         - NVIDIA NeMo checkpoints
- {base}/torch/        - PyTorch hub cache

Environment Variables:
- DALSTON_MODEL_DIR: Base model directory
  - Docker: defaults to /models (mounted volume)
  - Local: defaults to ~/.cache/dalston/models
- HF_HUB_CACHE: Overrides HuggingFace cache location
"""

from __future__ import annotations

import os
from pathlib import Path


def _get_default_model_dir() -> Path:
    """Get the default model directory based on environment.

    Returns:
        - /models if it exists (Docker with mounted volume)
        - ~/.cache/dalston/models otherwise (local development)
    """
    docker_path = Path("/models")
    if docker_path.exists() and docker_path.is_dir():
        return docker_path

    # Local development: use XDG cache directory
    xdg_cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(xdg_cache) / "dalston" / "models"


# Base model directory
MODEL_BASE = Path(os.environ.get("DALSTON_MODEL_DIR", str(_get_default_model_dir())))

# Per-framework subdirectories
HF_CACHE = Path(os.environ.get("HF_HUB_CACHE", str(MODEL_BASE / "huggingface")))
CTRANSLATE2_CACHE = MODEL_BASE / "ctranslate2"
NEMO_CACHE = MODEL_BASE / "nemo"
TORCH_CACHE = MODEL_BASE / "torch"


def get_hf_model_path(model_id: str) -> Path:
    """Get the expected path for a HuggingFace Hub model.

    Args:
        model_id: HuggingFace model ID (e.g., "Systran/faster-whisper-large-v3")

    Returns:
        Path to model directory in HF cache structure
    """
    # HuggingFace hub uses models--{org}--{name} format
    safe_id = model_id.replace("/", "--")
    return HF_CACHE / "hub" / f"models--{safe_id}"


def get_ctranslate2_model_path(model_id: str, subdir: str = "faster-whisper") -> Path:
    """Get the expected path for a CTranslate2 model.

    Args:
        model_id: Model identifier (e.g., "large-v3-turbo")
        subdir: Subdirectory for model type (default: "faster-whisper")

    Returns:
        Path to model directory
    """
    return CTRANSLATE2_CACHE / subdir / model_id


def get_nemo_model_path(model_id: str) -> Path:
    """Get the expected path for a NeMo model.

    Args:
        model_id: NeMo model identifier

    Returns:
        Path to model directory
    """
    # NeMo uses flat structure with underscores
    safe_id = model_id.replace("/", "_")
    return NEMO_CACHE / safe_id


def is_model_cached(model_id: str, framework: str = "huggingface") -> bool:
    """Check if a model is already downloaded.

    Args:
        model_id: Model identifier
        framework: One of "huggingface", "ctranslate2", "nemo"

    Returns:
        True if model exists on disk
    """
    if framework == "huggingface":
        path = get_hf_model_path(model_id)
        # HF cache has snapshots directory when download is complete
        return (path / "snapshots").exists()
    elif framework == "ctranslate2":
        path = get_ctranslate2_model_path(model_id)
        # CTranslate2 models have model.bin
        return (path / "model.bin").exists()
    elif framework == "nemo":
        path = get_nemo_model_path(model_id)
        # NeMo models have .nemo file
        return path.exists() and any(path.glob("*.nemo"))
    return False


def ensure_cache_dirs() -> None:
    """Create cache directories if they don't exist.

    Call this at engine startup to ensure the directory structure exists.
    """
    for cache_dir in [HF_CACHE, CTRANSLATE2_CACHE, NEMO_CACHE, TORCH_CACHE]:
        cache_dir.mkdir(parents=True, exist_ok=True)


def get_model_size(model_id: str, framework: str = "huggingface") -> int | None:
    """Get the size of a cached model in bytes.

    Args:
        model_id: Model identifier
        framework: One of "huggingface", "ctranslate2", "nemo"

    Returns:
        Size in bytes, or None if model not found
    """
    if framework == "huggingface":
        path = get_hf_model_path(model_id)
    elif framework == "ctranslate2":
        path = get_ctranslate2_model_path(model_id)
    elif framework == "nemo":
        path = get_nemo_model_path(model_id)
    else:
        return None

    if not path.exists():
        return None

    total_size = 0
    for f in path.rglob("*"):
        if f.is_file():
            total_size += f.stat().st_size

    return total_size


# Environment variable exports for engine Dockerfiles
ENV_EXPORTS = """
# Standardized model cache environment variables
ENV DALSTON_MODEL_DIR=/models
ENV HF_HUB_CACHE=/models/huggingface
ENV HF_HOME=/models/huggingface
ENV TORCH_HOME=/models/torch
ENV NEMO_CACHE=/models/nemo
ENV WHISPER_MODELS_DIR=/models/ctranslate2/faster-whisper
""".strip()
