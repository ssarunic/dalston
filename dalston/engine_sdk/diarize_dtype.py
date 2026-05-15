"""Mixed-precision dtype resolution for the diarization engine.

Reads ``DALSTON_DIARIZE_DTYPE`` (``auto`` | ``fp32`` | ``fp16`` | ``bf16``).
The default is ``fp32`` — operators must opt into mixed precision explicitly
once the per-GPU validation in M90.5 has produced acceptable drift/DER
numbers.  Setting the env var to ``auto`` picks the fastest *safe* dtype
for the current GPU:

* bf16 on Ampere+ (A10G, L4, A100, H100 — ``torch.cuda.is_bf16_supported()``)
* fp16 on Turing (T4 — Volta+ has fp16 Tensor Cores)
* fp32 elsewhere

A request for bf16 on hardware that lacks native bf16 support falls back
to fp16 (with a warning) rather than silently using the emulated bf16
path, which is slower than fp32.

Why autocast instead of ``model.half()``:
    Pyannote's pipeline mixes a Conformer-style segmentation net, a ResNet34
    embedding net, and CPU-bound clustering (VBx / AHC).  ``torch.autocast``
    keeps weights in fp32 and only casts activations on whitelist ops, so
    softmax accumulators and layer-norm stats stay in fp32 — eliminating the
    NaN class of failures that bites a naive ``.half()`` cast on quiet audio.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Literal

import structlog

logger = structlog.get_logger()

DTypeName = Literal["fp32", "fp16", "bf16"]
DTypeInput = Literal["auto", "fp32", "fp16", "bf16"]
_VALID_DTYPES: frozenset[str] = frozenset({"auto", "fp32", "fp16", "bf16"})


def resolve_diarize_dtype(override: DTypeInput | str | None = None) -> DTypeName:
    """Resolve the dtype to use for diarization inference.

    Resolution order:

      1. ``override`` argument (used by the per-job ``dtype`` config field)
      2. ``DALSTON_DIARIZE_DTYPE`` environment variable
      3. auto-detect based on ``torch.cuda`` capabilities

    Args:
        override: Explicit dtype request (``auto``/``fp32``/``fp16``/``bf16``),
            or ``None`` to defer to the env var.

    Returns:
        One of ``"fp32"``, ``"fp16"``, or ``"bf16"``.
    """
    # Default is fp32 — mixed precision is opt-in until the M90.5 per-GPU
    # validation lands. Operators flip to "auto" (or fp16/bf16 explicitly)
    # via DALSTON_DIARIZE_DTYPE once results are in.
    requested = (override or os.environ.get("DALSTON_DIARIZE_DTYPE", "fp32")).lower()
    if requested not in _VALID_DTYPES:
        logger.warning("invalid_diarize_dtype", requested=requested, fallback="fp32")
        requested = "fp32"

    try:
        import torch
    except ImportError:
        return "fp32"

    if not torch.cuda.is_available():
        return "fp32"

    # Capability probes are deferred because each call hits the CUDA driver
    # (cudaGetDeviceProperties). When the caller passes an explicit dtype
    # that resolves directly, we skip them entirely.
    if requested == "auto":
        if torch.cuda.is_bf16_supported():
            return "bf16"
        cap_major, _ = torch.cuda.get_device_capability()
        return "fp16" if cap_major >= 7 else "fp32"

    if requested == "bf16":
        if torch.cuda.is_bf16_supported():
            return "bf16"
        cap_major, _ = torch.cuda.get_device_capability()
        fallback: DTypeName = "fp16" if cap_major >= 7 else "fp32"
        logger.warning(
            "bf16_unsupported_falling_back",
            device_cap_major=cap_major,
            fallback=fallback,
        )
        return fallback

    if requested == "fp16":
        cap_major, _ = torch.cuda.get_device_capability()
        if cap_major >= 7:  # Volta and newer have fp16 Tensor Cores
            return "fp16"
        logger.warning("fp16_unsupported_falling_back", device_cap_major=cap_major)
        return "fp32"

    return "fp32"


@contextmanager
def autocast_for_diarize(dtype_name: DTypeName) -> Iterator[None]:
    """Yield a ``torch.autocast`` context for fp16/bf16; nullcontext for fp32.

    Importing torch lazily inside the function keeps the helper usable from
    code paths that may not have torch available (e.g. unit tests, CPU-only
    environments where ``dtype_name`` is always ``fp32``).
    """
    if dtype_name == "fp32":
        with nullcontext():
            yield
        return

    import torch

    torch_dtype = torch.float16 if dtype_name == "fp16" else torch.bfloat16
    with torch.autocast("cuda", dtype=torch_dtype):
        yield
