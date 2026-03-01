"""Voice Activity Detection using Silero VAD.

Provides speech detection and endpoint detection for streaming audio,
determining when to trigger ASR transcription.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

import numpy as np
import structlog

logger = structlog.get_logger()


@dataclass
class VADConfig:
    """VAD configuration parameters.

    Attributes:
        speech_threshold: Probability threshold for speech detection (0.0-1.0).
            Lower values are more sensitive. Default: 0.5
        min_speech_duration: Minimum speech duration in seconds before
            considering it valid speech. Default: 0.25
        min_silence_duration: Silence duration in seconds to trigger
            endpoint (end of utterance). Default: 0.5
        sample_rate: Expected audio sample rate. Default: 16000
        lookback_chunks: Number of chunks to buffer for capturing speech onset.
            At 100ms chunks, 3 = ~300ms lookback. Default: 3
    """

    speech_threshold: float = 0.5
    min_speech_duration: float = 0.25
    min_silence_duration: float = 0.5
    sample_rate: int = 16000
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
        self._model = None
        self._state = VADState.SILENCE
        self._speech_buffer: list[np.ndarray] = []
        self._lookback_buffer: list[np.ndarray] = []
        self._silence_duration: float = 0.0
        self._speech_duration: float = 0.0

    def _load_model(self) -> None:
        """Load Silero VAD model lazily."""
        if self._model is not None:
            return

        try:
            import torch

            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                trust_repo=True,
            )
            self._model = model
            self._get_speech_timestamps = utils[0]
            logger.info("silero_vad_loaded")
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
        import torch

        self._load_model()

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
        with torch.no_grad():
            for i in range(0, len(audio) - window_size + 1, window_size):
                window = audio[i : i + window_size]
                audio_tensor = torch.from_numpy(window)
                prob = self._model(audio_tensor, self.config.sample_rate).item()
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
