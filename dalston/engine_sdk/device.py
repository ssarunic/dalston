"""Centralized device detection for Dalston engines.

All engines should use ``detect_device()`` instead of implementing their
own detection logic.  This ensures consistent behaviour across the fleet:

- Reads ``DALSTON_DEVICE`` env var (``cuda``, ``mps``, ``cpu``, ``auto``, or empty).
- Auto-detect order: CUDA → MPS → CPU.
- Raises on impossible requests (e.g. ``cuda`` when CUDA is unavailable).
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger()


def detect_device(*, include_mps: bool = True) -> str:
    """Resolve the inference device from ``DALSTON_DEVICE`` with auto-detect fallback.

    Args:
        include_mps: Whether to consider MPS (Apple Silicon) during auto-detection.
            Set to ``False`` for frameworks that don't support MPS (e.g. NeMo, ONNX Runtime).

    Returns:
        One of ``"cuda"``, ``"mps"``, or ``"cpu"``.

    Raises:
        RuntimeError: If the requested device is not available.
        ValueError: If ``DALSTON_DEVICE`` contains an unrecognised value.
    """
    requested = os.environ.get("DALSTON_DEVICE", "").lower()

    cuda_available = False
    mps_available = False
    try:
        import torch

        cuda_available = torch.cuda.is_available()
        mps_available = include_mps and torch.backends.mps.is_available()
    except ImportError:
        pass

    # Fallback: check onnxruntime CUDA support (for engines without PyTorch)
    if not cuda_available:
        try:
            import onnxruntime

            cuda_available = (
                "CUDAExecutionProvider" in onnxruntime.get_available_providers()
            )
        except ImportError:
            pass

    if requested == "cpu":
        logger.info("device_forced_cpu")
        return "cpu"

    if requested == "cuda":
        if not cuda_available:
            raise RuntimeError("DALSTON_DEVICE=cuda but CUDA is not available.")
        logger.info("device_using_cuda")
        return "cuda"

    if requested == "mps":
        if not mps_available:
            raise RuntimeError(
                "DALSTON_DEVICE=mps but MPS is not available"
                + (" (or disabled for this engine)." if not include_mps else ".")
            )
        logger.info("device_using_mps")
        return "mps"

    if requested not in ("", "auto"):
        raise ValueError(
            f"Unknown DALSTON_DEVICE value: {requested!r}. Use cuda, mps, or cpu."
        )

    # Auto-detect: CUDA → MPS → CPU
    if cuda_available:
        logger.info("device_auto_cuda")
        return "cuda"
    if mps_available:
        logger.info("device_auto_mps")
        return "mps"

    logger.info("device_auto_cpu")
    return "cpu"
