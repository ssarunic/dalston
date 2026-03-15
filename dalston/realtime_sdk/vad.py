"""Voice Activity Detection using Silero VAD (ONNX Runtime).

Provides speech detection and endpoint detection for streaming audio,
determining when to trigger ASR transcription.

Uses ONNX Runtime for Silero VAD inference, eliminating the PyTorch
dependency. The ONNX model is downloaded on first use and cached locally.
"""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import numpy as np
import structlog

from dalston.common.audio_defaults import (
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_MIN_SPEECH_MS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VAD_THRESHOLD,
)

logger = structlog.get_logger()

# Pinned Silero VAD ONNX model URL (v5.1.2) for reproducible builds.
_SILERO_VAD_ONNX_URL = (
    "https://github.com/snakers4/silero-vad/raw/v5.1.2"
    "/src/silero_vad/data/silero_vad.onnx"
)

# Cache directory for the downloaded model file.
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "dalston" / "silero-vad"


def _get_model_path() -> Path:
    """Resolve and ensure the Silero VAD ONNX model is available.

    Checks (in order):
    1. ``DALSTON_SILERO_VAD_ONNX`` env var pointing to a local file.
    2. Cached download under ``~/.cache/dalston/silero-vad/silero_vad.onnx``.
    3. Downloads from GitHub if not cached.

    Returns:
        Path to the ONNX model file.

    Raises:
        RuntimeError: If the model cannot be obtained.
    """
    # Allow explicit override for containerised / air-gapped deployments.
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
        # Clean up partial download.
        model_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Failed to download Silero VAD ONNX model from {_SILERO_VAD_ONNX_URL}: {exc}"
        ) from exc

    logger.info("silero_vad_onnx_downloaded", path=str(model_path))
    return model_path


class _SileroOnnxModel:
    """Thin wrapper around the Silero VAD v5 ONNX model.

    Manages the ONNX Runtime session and the recurrent hidden state /
    context window that must be carried across successive chunk calls.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self._state: np.ndarray = np.zeros((2, 1, 128), dtype=np.float32)
        self._context: np.ndarray = np.zeros(0, dtype=np.float32)
        self._last_sr: int = 0
        self._last_batch_size: int = 0

    # -- public API used by VADProcessor ------------------------------------

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
        context_size = 64 if sample_rate == 16000 else 32

        # Reset internal state when sample rate or batch size changes.
        if sample_rate != self._last_sr or batch_size != self._last_batch_size:
            self.reset_states(batch_size)
            self._last_sr = sample_rate
            self._last_batch_size = batch_size

        # Initialise context on the very first call.
        if self._context.size == 0:
            self._context = np.zeros((batch_size, context_size), dtype=np.float32)

        # Prepend context from the previous chunk (Silero v5 requirement).
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


@dataclass
class VADConfig:
    """VAD configuration parameters.

    Attributes:
        speech_threshold: Probability threshold for speech detection (0.0-1.0).
            Lower values are more sensitive. Default: 0.5
        min_speech_duration: Minimum speech duration in seconds before
            considering it valid speech. Default: 0.25
        min_silence_duration: Silence duration in seconds to trigger
            endpoint (end of utterance). Default: 0.4
        sample_rate: Expected audio sample rate. Default: 16000
        lookback_chunks: Number of chunks to buffer for capturing speech onset.
            At 100ms chunks, 3 = ~300ms lookback. Default: 3
    """

    speech_threshold: float = DEFAULT_VAD_THRESHOLD
    min_speech_duration: float = DEFAULT_MIN_SPEECH_MS / 1000.0
    min_silence_duration: float = DEFAULT_MIN_SILENCE_MS / 1000.0
    sample_rate: int = DEFAULT_SAMPLE_RATE
    lookback_chunks: int = 3


class VADState(Enum):
    """VAD state machine states."""

    SILENCE = "silence"
    SPEECH = "speech"


@dataclass
class VADResult:
    """Result from processing an audio chunk.

    Attributes:
        event: Event type if state transition occurred, None otherwise
        speech_audio: Accumulated speech audio if endpoint detected (speech_end),
            None otherwise. This is the complete utterance to transcribe.
    """

    event: Literal["speech_start", "speech_end"] | None
    speech_audio: np.ndarray | None = None


class VADProcessor:
    """Silero VAD wrapper with endpoint detection.

    Processes streaming audio chunks and detects:
    - Speech start: when voice activity begins
    - Speech end (endpoint): when sufficient silence follows speech

    The processor maintains a lookback buffer to capture the beginning
    of speech that might be cut off by chunk boundaries.

    Uses the ONNX Runtime backend for Silero VAD inference (no PyTorch
    dependency required).

    Example:
        vad = VADProcessor()

        for chunk in audio_chunks:
            result = vad.process_chunk(chunk)

            if result.event == "speech_start":
                # Voice activity detected
                pass
            elif result.event == "speech_end":
                # Endpoint detected, transcribe the utterance
                transcribe(result.speech_audio)

        # Session end - flush any remaining speech
        remaining = vad.flush()
        if remaining is not None:
            transcribe(remaining)
    """

    def __init__(self, config: VADConfig | None = None) -> None:
        """Initialize VAD processor.

        Args:
            config: VAD configuration. Uses defaults if not provided.
        """
        self.config = config or VADConfig()
        self._model: _SileroOnnxModel | None = None
        self._state = VADState.SILENCE
        self._speech_buffer: list[np.ndarray] = []
        self._lookback_buffer: list[np.ndarray] = []
        self._silence_duration: float = 0.0
        self._speech_duration: float = 0.0

    def _load_model(self) -> None:
        """Load Silero VAD ONNX model lazily."""
        if self._model is not None:
            return

        try:
            import onnxruntime as ort
        except ImportError as e:
            raise RuntimeError(
                "onnxruntime is required for Silero VAD but not installed. "
                "Install with: pip install onnxruntime (CPU) or onnxruntime-gpu (GPU)"
            ) from e

        try:
            model_path = _get_model_path()
            session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
            )
            self._model = _SileroOnnxModel(session)
            logger.info("silero_vad_loaded", backend="onnxruntime")
        except Exception as e:
            logger.error("silero_vad_load_failed", error=str(e))
            raise RuntimeError(f"Failed to load Silero VAD model: {e}") from e

    def _get_speech_prob(self, audio: np.ndarray) -> float:
        """Get speech probability for audio chunk.

        Args:
            audio: Audio samples as float32 numpy array

        Returns:
            Speech probability between 0.0 and 1.0
        """
        self._load_model()
        assert self._model is not None  # for type checker

        # Ensure audio is float32 and in range [-1, 1]
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Silero VAD requires exactly 512 samples at 16kHz (or 256 at 8kHz)
        # Process in windows and return max probability
        window_size = 512 if self.config.sample_rate == 16000 else 256

        if len(audio) < window_size:
            # Pad short audio with zeros
            audio = np.pad(audio, (0, window_size - len(audio)))

        # Process all windows and return max probability
        max_prob = 0.0
        for i in range(0, len(audio) - window_size + 1, window_size):
            window = audio[i : i + window_size]
            prob = self._model(window, self.config.sample_rate)
            max_prob = max(max_prob, prob)

        return max_prob

    def process_chunk(self, audio: np.ndarray) -> VADResult:
        """Process audio chunk and detect speech boundaries.

        Args:
            audio: Audio samples as float32 numpy array, mono, at config.sample_rate

        Returns:
            VADResult with event type and speech audio if endpoint detected
        """
        chunk_duration = len(audio) / self.config.sample_rate

        # Get speech probability
        prob = self._get_speech_prob(audio)
        is_speech = prob > self.config.speech_threshold

        # Update lookback buffer (for capturing speech onset)
        self._lookback_buffer.append(audio.copy())
        if len(self._lookback_buffer) > self.config.lookback_chunks:
            self._lookback_buffer.pop(0)

        # State machine
        if self._state == VADState.SILENCE:
            if is_speech:
                # Transition to speech state
                self._state = VADState.SPEECH
                self._speech_duration = chunk_duration
                self._silence_duration = 0.0

                # Include lookback buffer to capture speech onset
                self._speech_buffer = list(self._lookback_buffer)

                logger.debug("speech_start_detected", probability=round(prob, 2))
                return VADResult(event="speech_start")
            else:
                # Still silence
                return VADResult(event=None)

        elif self._state == VADState.SPEECH:
            if is_speech:
                # Continue speech
                self._speech_buffer.append(audio.copy())
                self._speech_duration += chunk_duration
                self._silence_duration = 0.0
                return VADResult(event=None)
            else:
                # Silence during speech - potential endpoint
                self._speech_buffer.append(audio.copy())
                self._silence_duration += chunk_duration

                if self._silence_duration >= self.config.min_silence_duration:
                    # Endpoint detected - end of utterance
                    if self._speech_duration >= self.config.min_speech_duration:
                        # Valid utterance, return accumulated speech
                        speech_audio = np.concatenate(self._speech_buffer)
                        self._reset_speech_state()

                        logger.debug(
                            "speech_end_detected",
                            duration=round(
                                len(speech_audio) / self.config.sample_rate, 2
                            ),
                        )
                        return VADResult(event="speech_end", speech_audio=speech_audio)
                    else:
                        # Too short, discard
                        logger.debug("speech_too_short_discarding")
                        self._reset_speech_state()
                        return VADResult(event="speech_end", speech_audio=None)

                # Still in potential endpoint, keep buffering
                return VADResult(event=None)

        return VADResult(event=None)

    def _reset_speech_state(self) -> None:
        """Reset speech-related state after endpoint."""
        self._state = VADState.SILENCE
        self._speech_buffer.clear()
        self._silence_duration = 0.0
        self._speech_duration = 0.0

    def flush(self) -> np.ndarray | None:
        """Flush any buffered speech audio.

        Call this at session end to get any remaining speech
        that hasn't reached an endpoint.

        Returns:
            Accumulated speech audio if any, None otherwise
        """
        if self._state == VADState.SPEECH and self._speech_buffer:
            if self._speech_duration >= self.config.min_speech_duration:
                speech_audio = np.concatenate(self._speech_buffer)
                self._reset_speech_state()
                logger.debug(
                    "flushed_remaining_speech",
                    duration=round(len(speech_audio) / self.config.sample_rate, 2),
                )
                return speech_audio

        self._reset_speech_state()
        return None

    def clear(self) -> None:
        """Clear (discard) any buffered speech audio without processing.

        Used for OpenAI-compatible input_audio_buffer.clear operation.
        Discards accumulated audio and returns to silence state.
        """
        if self._speech_buffer:
            discarded_samples = sum(len(chunk) for chunk in self._speech_buffer)
            logger.debug(
                "cleared_speech_buffer",
                discarded_duration=round(
                    discarded_samples / self.config.sample_rate, 2
                ),
            )
        self._reset_speech_state()

    def reset(self) -> None:
        """Reset VAD state for a new session.

        Clears all buffers and returns to silence state.
        """
        self._state = VADState.SILENCE
        self._speech_buffer.clear()
        self._lookback_buffer.clear()
        self._silence_duration = 0.0
        self._speech_duration = 0.0
        # Reset model state if needed
        if self._model is not None:
            self._model.reset_states()

    @property
    def state(self) -> VADState:
        """Current VAD state."""
        return self._state

    @property
    def is_speaking(self) -> bool:
        """Whether currently in speech state."""
        return self._state == VADState.SPEECH

    def get_speech_buffer_samples(self) -> int:
        """Get total samples in speech buffer.

        Returns:
            Total number of audio samples accumulated in speech buffer
        """
        return sum(len(chunk) for chunk in self._speech_buffer)

    def force_endpoint(self) -> np.ndarray | None:
        """Force an endpoint and return accumulated speech.

        Used when max utterance duration is exceeded. Returns the
        accumulated speech and resets to SPEECH state (not SILENCE)
        since speech is continuing.

        Returns:
            Accumulated speech audio if any, None otherwise
        """
        if not self._speech_buffer:
            return None

        speech_audio = np.concatenate(self._speech_buffer)
        duration = len(speech_audio) / self.config.sample_rate

        logger.debug(
            "forced_endpoint",
            duration=round(duration, 2),
        )

        # Reset buffers but stay in SPEECH state (speech is continuing)
        self._speech_buffer.clear()
        self._speech_duration = 0.0
        self._silence_duration = 0.0
        # State remains SPEECH - caller sends speech_start to indicate continuation

        return speech_audio
