"""Shared Silero VAD v5 ONNX loader and inference wrapper.

Centralises the bits that realtime streaming VAD and the batch
long-audio chunker both need:

- :func:`get_silero_onnx_path` resolves the ONNX model file path,
  honouring ``DALSTON_SILERO_VAD_ONNX`` (for images that bake the
  weights), a local cache directory, and a one-shot GitHub download as
  a last resort.
- :func:`load_silero_session` opens an ``onnxruntime`` InferenceSession
  against that file.
- :class:`SileroOnnxModel` wraps the session and manages the recurrent
  state + context window that Silero v5 requires across successive
  chunk calls. Supports 8 kHz and 16 kHz input and resets cleanly when
  the caller switches streams via :meth:`reset_states`.

Callers:

- ``dalston/realtime_sdk/vad.py`` — streaming VAD state machine
  (100 ms chunks, endpoint detection).
- ``dalston/engine_sdk/vad.py`` — offline scan fallback in
  :class:`VadChunker` when the ``silero_vad`` pip package is not
  importable but a pre-baked ONNX file is available.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger()


# Pinned Silero VAD ONNX model URL (v5.1.2) for reproducible builds.
_SILERO_VAD_ONNX_URL = (
    "https://github.com/snakers4/silero-vad/raw/v5.1.2"
    "/src/silero_vad/data/silero_vad.onnx"
)

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "dalston" / "silero-vad"

# Silero v5 window sizes per sample rate (samples per VAD frame).
WINDOW_SAMPLES_16K = 512
WINDOW_SAMPLES_8K = 256

# Silero v5 context window per sample rate (prepended to each frame).
CONTEXT_SAMPLES_16K = 64
CONTEXT_SAMPLES_8K = 32


def get_silero_onnx_path() -> Path:
    """Resolve and ensure the Silero VAD ONNX model is available.

    Resolution order:

    1. ``DALSTON_SILERO_VAD_ONNX`` env var pointing to a local file.
    2. Cached download under ``~/.cache/dalston/silero-vad/silero_vad.onnx``
       (override root via ``DALSTON_MODEL_CACHE``).
    3. One-shot download from GitHub if the cache is empty.

    Raises:
        RuntimeError: when the env override is invalid or the download
            fails and no cached copy exists.
    """
    env_path = os.environ.get("DALSTON_SILERO_VAD_ONNX")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        raise RuntimeError(
            f"DALSTON_SILERO_VAD_ONNX={env_path} does not exist or is not a file"
        )

    cache_dir = Path(os.environ.get("DALSTON_MODEL_CACHE", _DEFAULT_CACHE_DIR))
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = cache_dir / "silero_vad.onnx"

    if model_path.is_file():
        return model_path

    logger.info("downloading_silero_vad_onnx", url=_SILERO_VAD_ONNX_URL)
    try:
        urllib.request.urlretrieve(_SILERO_VAD_ONNX_URL, str(model_path))
    except Exception as exc:
        model_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Failed to download Silero VAD ONNX model from {_SILERO_VAD_ONNX_URL}: {exc}"
        ) from exc

    logger.info("silero_vad_onnx_downloaded", path=str(model_path))
    return model_path


def load_silero_session(model_path: Path | None = None) -> Any:
    """Open an onnxruntime session against the Silero v5 VAD model.

    Args:
        model_path: Optional explicit path. When omitted, resolves via
            :func:`get_silero_onnx_path`.

    Returns:
        An ``onnxruntime.InferenceSession`` configured with the CPU
        execution provider. VAD is cheap and CPU-only is both
        sufficient and portable.

    Raises:
        RuntimeError: if ``onnxruntime`` is not installed or the session
            cannot be constructed.
    """
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "onnxruntime is required for Silero VAD but not installed. "
            "Install with: pip install onnxruntime (CPU) or onnxruntime-gpu (GPU)"
        ) from exc

    resolved = model_path if model_path is not None else get_silero_onnx_path()
    try:
        return ort.InferenceSession(
            str(resolved),
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to load Silero VAD model: {exc}") from exc


class SileroOnnxModel:
    """Thin wrapper around the Silero VAD v5 ONNX model.

    Manages the ONNX Runtime session and the recurrent hidden state /
    context window that must be carried across successive chunk calls.
    Supports 8 kHz (256-sample windows, 32-sample context) and 16 kHz
    (512-sample windows, 64-sample context). Resetting happens
    automatically when the sample rate or batch size changes between
    calls; callers switching to a new stream should call
    :meth:`reset_states` explicitly to avoid contaminating the new
    stream with the previous one's final context.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self._state: np.ndarray = np.zeros((2, 1, 128), dtype=np.float32)
        self._context: np.ndarray = np.zeros(0, dtype=np.float32)
        self._last_sr: int = 0
        self._last_batch_size: int = 0

    def __call__(self, audio: np.ndarray, sample_rate: int) -> float:
        """Return speech probability for a single audio window.

        Args:
            audio: 1-D float32 array of ``window_size`` samples
                   (512 @ 16 kHz, 256 @ 8 kHz).
            sample_rate: 16000 or 8000.

        Returns:
            Speech probability in [0.0, 1.0].
        """
        if audio.ndim == 1:
            audio = audio.reshape(1, -1)

        batch_size = audio.shape[0]
        context_size = (
            CONTEXT_SAMPLES_16K if sample_rate == 16000 else CONTEXT_SAMPLES_8K
        )

        if sample_rate != self._last_sr or batch_size != self._last_batch_size:
            self.reset_states(batch_size)
            self._last_sr = sample_rate
            self._last_batch_size = batch_size

        if self._context.size == 0:
            self._context = np.zeros((batch_size, context_size), dtype=np.float32)

        # Silero v5 requires the previous frame's tail (context) to be
        # prepended to each new frame.
        x = np.concatenate([self._context, audio.astype(np.float32)], axis=1)

        ort_inputs = {
            "input": x,
            "state": self._state,
            "sr": np.array(sample_rate, dtype=np.int64),
        }

        out, state = self._session.run(None, ort_inputs)
        self._state = state
        self._context = x[..., -context_size:]

        return float(out[0][0])

    def reset_states(self, batch_size: int = 1) -> None:
        """Reset recurrent state for a new audio stream."""
        self._state = np.zeros((2, batch_size, 128), dtype=np.float32)
        self._context = np.zeros(0, dtype=np.float32)
        self._last_sr = 0
        self._last_batch_size = 0
