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


def get_vad_threshold() -> float:
    """Silero speech-probability threshold, tunable via DALSTON_VAD_THRESHOLD.

    One knob for every Silero consumer (M92.7). The 0.5 default under-detects
    on narrowband/low-passed telephony audio — 0.3 is a good starting point
    there. Out-of-range or malformed values fall back to the default.
    """
    import os

    raw = os.environ.get("DALSTON_VAD_THRESHOLD")
    if raw is None:
        return DEFAULT_VAD_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_VAD_THRESHOLD
    if not 0.0 < value < 1.0:
        return DEFAULT_VAD_THRESHOLD
    return value


# =============================================================================
# Utterance Processing
# =============================================================================

DEFAULT_MAX_UTTERANCE_SECONDS = 30.0  # maximum single utterance duration
DEFAULT_PRE_SPEECH_PAD_MS = 250  # audio to include before detected speech
DEFAULT_POST_SPEECH_PAD_MS = 50  # audio to include after detected speech

# =============================================================================
# Resampling
# =============================================================================

# Maps user-facing profile names to soxr quality presets.
# "fast"     — MQ: low CPU, acceptable for telephony-grade input (8 kHz μ-law).
# "balanced" — HQ: good default for most real-time paths.
# "high"     — VHQ: broadcast quality, higher CPU cost.
RESAMPLE_QUALITY_PROFILES: dict[str, str] = {
    "fast": "MQ",
    "balanced": "HQ",
    "high": "VHQ",
}
DEFAULT_RESAMPLE_QUALITY = "balanced"

# =============================================================================
# Buffer Limits
# =============================================================================

MAX_RAW_AUDIO_BUFFER_BYTES = 10 * 1024 * 1024  # 10MB - max buffered audio
