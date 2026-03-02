"""Audio processing default values.

Centralizes audio configuration defaults used across gateway and realtime
components for consistency.
"""

# =============================================================================
# Sample Rate
# =============================================================================

DEFAULT_SAMPLE_RATE = 16000  # Hz - standard for speech recognition models
MIN_SAMPLE_RATE = 8000  # Hz - minimum supported
MAX_SAMPLE_RATE = 48000  # Hz - maximum supported

# =============================================================================
# Voice Activity Detection (VAD)
# =============================================================================

DEFAULT_VAD_THRESHOLD = 0.5  # probability threshold for speech detection
DEFAULT_VAD_NEG_THRESHOLD = 0.25  # threshold to confirm silence (hysteresis)
DEFAULT_MIN_SPEECH_MS = 250  # minimum speech duration to consider valid
DEFAULT_MIN_SILENCE_MS = 400  # minimum silence to end an utterance

# =============================================================================
# Utterance Processing
# =============================================================================

DEFAULT_MAX_UTTERANCE_SECONDS = 30.0  # maximum single utterance duration
DEFAULT_PRE_SPEECH_PAD_MS = 250  # audio to include before detected speech
DEFAULT_POST_SPEECH_PAD_MS = 50  # audio to include after detected speech

# =============================================================================
# Buffer Limits
# =============================================================================

MAX_RAW_AUDIO_BUFFER_BYTES = 10 * 1024 * 1024  # 10MB - max buffered audio
