"""GPU OOM detection and recovery utilities.

Provides helpers for catching CUDA/MPS/ONNX out-of-memory errors and
clearing GPU cache. Used by both ONNX and faster-whisper inference
to implement binary backoff on OOM.
"""

from __future__ import annotations

import gc

import structlog

logger = structlog.get_logger()


def is_oom_error(exc: BaseException) -> bool:
    """Check if an exception is a GPU out-of-memory error.

    Detects both PyTorch CUDA OOM and ONNX Runtime allocation failures.
    """
    msg = str(exc).lower()
    # Check for specific OOM exception types first
    exc_type = type(exc).__name__.lower()
    if "outofmemory" in exc_type:
        return True
    # Check message patterns — require both an allocation keyword and a
    # memory/size keyword to avoid false positives on unrelated errors
    has_alloc = any(p in msg for p in ("allocate", "out of memory", "outofmemory"))
    has_context = any(p in msg for p in ("cuda", "gpu", "onnx", "bfc_arena", "device"))
    return has_alloc and has_context


def clear_gpu_cache() -> None:
    """Best-effort GPU memory cleanup after OOM."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except ImportError:
        pass
