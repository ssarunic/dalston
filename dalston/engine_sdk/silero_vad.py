"""Shared Silero VAD v5 loaders and inference wrappers.

Centralises the bits that realtime streaming VAD and the batch
long-audio chunker both need:

- :func:`load_silero_model` picks a backend from whatever the image
  already ships — TorchScript first, ONNX second — and is what callers
  should use unless they specifically need one runtime.
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
- :class:`SileroTorchModel` is the TorchScript equivalent, presenting the
  same :class:`SileroModel` interface.

Neither ``torch`` nor ``onnxruntime`` is imported at module scope: this
module is imported on images that have only one of them (``base-onnx``
ships ONNX without torch; ``base-nemo`` ships torch). Both imports live
inside the functions that need them.

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
from typing import TYPE_CHECKING, Any, Protocol

import structlog

# numpy is engine-only — see comment in engine_sdk/audio.py.
if TYPE_CHECKING:
    import numpy as np

logger = structlog.get_logger()


# Pinned Silero VAD ONNX model URL for reproducible builds.
#
# M100.6: this tag must match the ``silero-vad`` pin in every
# ``docker/Dockerfile.base-*``. The two backends load different files —
# the package's bundled TorchScript weights vs this ONNX export — and
# they are only the same model when both come from one release. Move
# them together or backend selection silently changes VAD behaviour.
_SILERO_VAD_VERSION = "v6.2.1"
_SILERO_VAD_ONNX_URL = (
    f"https://github.com/snakers4/silero-vad/raw/{_SILERO_VAD_VERSION}"
    "/src/silero_vad/data/silero_vad.onnx"
)

_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "dalston" / "silero-vad"

# Silero v5 window sizes per sample rate (samples per VAD frame).
WINDOW_SAMPLES_16K = 512
WINDOW_SAMPLES_8K = 256

# Silero v5 context window per sample rate (prepended to each frame).
CONTEXT_SAMPLES_16K = 64
CONTEXT_SAMPLES_8K = 32


class SileroModel(Protocol):
    """Interface shared by the ONNX and TorchScript VAD backends.

    Both :class:`SileroOnnxModel` and :class:`SileroTorchModel` satisfy
    this structurally, so callers can hold either without caring which
    runtime the image happens to ship.
    """

    def __call__(self, audio: np.ndarray, sample_rate: int) -> float:
        """Return speech probability in [0.0, 1.0] for one audio window."""
        ...

    def reset_states(self, batch_size: int = 1) -> None:
        """Reset recurrent state before feeding a new audio stream."""
        ...


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
        import numpy as np

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
        import numpy as np

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
        import numpy as np

        self._state = np.zeros((2, batch_size, 128), dtype=np.float32)
        self._context = np.zeros(0, dtype=np.float32)
        self._last_sr = 0
        self._last_batch_size = 0


class SileroTorchModel:
    """Silero VAD v5 via the TorchScript model bundled in the silero-vad wheel.

    Presents the same interface as :class:`SileroOnnxModel`. Unlike the
    ONNX graph — which exposes its recurrent state as explicit inputs and
    outputs, forcing the caller to thread hidden state and the context
    window across calls — the TorchScript module owns that state
    internally. So this wrapper only adapts numpy <-> torch and
    normalises the return type.
    """

    def __init__(self, module: Any) -> None:
        self._module = module

    def __call__(self, audio: np.ndarray, sample_rate: int) -> float:
        """Return speech probability for a single audio window.

        Args:
            audio: 1-D float32 array of ``window_size`` samples
                   (512 @ 16 kHz, 256 @ 8 kHz). A ``(1, window_size)``
                   array is accepted for parity with the ONNX wrapper.
            sample_rate: 16000 or 8000.

        Returns:
            Speech probability in [0.0, 1.0].

        Raises:
            ValueError: if given a batch of more than one window.
        """
        import numpy as np
        import torch

        if audio.ndim > 1:
            # The ONNX wrapper accepts (batch, window); the TorchScript
            # module takes a single 1-D window. Collapse a singleton batch
            # rather than silently scoring only the first row.
            if audio.shape[0] != 1:
                raise ValueError(
                    "SileroTorchModel supports batch size 1, got "
                    f"{audio.shape[0]}. Use SileroOnnxModel for batched input."
                )
            audio = audio.reshape(-1)

        tensor = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
        # no_grad matters here, not just for speed: the module is recurrent
        # and stateful, so without it every realtime window allocates
        # autograd metadata and the graph can be retained through the
        # carried state for the life of the stream. Silero's own
        # VADIterator wraps inference the same way.
        with torch.no_grad():
            out = self._module(tensor, sample_rate)
        # .item() rather than float(): the module returns a tensor, and
        # float() on a grad-tracking one emits "Converting a tensor with
        # requires_grad=True to a scalar may lead to unexpected behavior".
        return float(out.item())

    def reset_states(self, batch_size: int = 1) -> None:
        """Reset recurrent state for a new audio stream.

        ``batch_size`` exists only for parity with
        :class:`SileroOnnxModel`. The TorchScript module manages state
        internally for a single stream, so anything other than 1 cannot
        be honoured — log it rather than pretend it was applied.
        """
        if batch_size != 1:
            logger.debug(
                "silero_torch_batch_size_ignored",
                batch_size=batch_size,
                message="TorchScript Silero backend supports a single stream",
            )
        self._module.reset_states()


def _try_load_torch_model() -> SileroTorchModel | None:
    """Load the TorchScript Silero model, or *None* if unavailable.

    Returns *None* — rather than raising — when torch or the
    ``silero_vad`` package is missing, so :func:`load_silero_model` can
    fall through to ONNX. A package that is present but fails to load is
    a real problem worth surfacing, so it is logged before falling back.
    """
    try:
        import torch  # noqa: F401
        from silero_vad import load_silero_vad
    except ImportError:
        return None

    try:
        # Defaults to the bundled TorchScript weights (onnx=False), which
        # ship inside the wheel — no download, works fully offline.
        module = load_silero_vad()
    except Exception as exc:
        logger.warning("silero_vad_torch_load_failed", error=str(exc)[:200])
        return None

    logger.info("silero_vad_loaded", backend="silero_vad_pkg")
    return SileroTorchModel(module)


def load_silero_model() -> SileroModel:
    """Load a Silero VAD v5 model on whichever runtime is available.

    Resolution order (first success wins):

    1. ``silero_vad`` pip package TorchScript model — preferred on
       torch-based images (``base-nemo``, ``base-pytorch``,
       ``base-pyannote``). Offline: the wheel bundles ``silero_vad.jit``.
    2. ``onnxruntime`` via :func:`load_silero_session` — for images that
       ship ONNX but no torch (``base-onnx``, ``base-engine``).

    The two backends are only equivalent when the image's ``silero-vad``
    pin and its baked ONNX file come from the *same* Silero release.
    They are genuinely different weights otherwise — ``silero_vad.onnx``
    has the same byte size at v5.1.2 and v6.2.1 but a different sha256,
    and the ``.jit`` differs in both — so a mismatched image silently
    changes speech probabilities and endpointing depending on which
    backend wins. Image Dockerfiles must move the pin and the baked ONNX
    URL together; see ``docker/Dockerfile.base-*``.

    Returns:
        A backend satisfying :class:`SileroModel`.

    Raises:
        RuntimeError: if neither backend is available, naming both
            install options.
    """
    torch_model = _try_load_torch_model()
    if torch_model is not None:
        return torch_model

    try:
        session = load_silero_session()
    except RuntimeError as exc:
        raise RuntimeError(
            "No Silero VAD backend available. Install either PyTorch plus "
            "the silero-vad package (pip install torch silero-vad) or "
            "onnxruntime (pip install onnxruntime / onnxruntime-gpu)."
        ) from exc

    logger.info("silero_vad_loaded", backend="onnxruntime")
    return SileroOnnxModel(session)
