"""Audio probing service for validating and extracting metadata from uploaded audio.

Uses tinytag to extract format, duration, sample rate, and channel count.
Validates audio before job creation to provide immediate feedback on invalid files.
"""

from dataclasses import dataclass
from io import BytesIO

import structlog
from tinytag import TinyTag, TinyTagException

logger = structlog.get_logger()

# Maximum duration we accept (10 hours)
MAX_DURATION_SECONDS = 36000

# Minimum duration (0.1 seconds)
MIN_DURATION_SECONDS = 0.1


@dataclass
class AudioMetadata:
    """Audio file metadata extracted from audio file."""

    format: str  # File format (e.g., "mp3", "wav", "flac")
    duration: float  # Duration in seconds
    sample_rate: int  # Sample rate in Hz (e.g., 44100, 48000)
    channels: int  # Number of audio channels (1=mono, 2=stereo)
    bit_depth: int | None  # Bits per sample (e.g., 16, 24, 32) - None for lossy formats


class AudioProbeError(Exception):
    """Error during audio probing."""

    pass


class InvalidAudioError(AudioProbeError):
    """Audio file is invalid or unsupported."""

    pass


def probe_audio(data: bytes, filename: str | None = None) -> AudioMetadata:
    """Probe audio data to extract metadata.

    Args:
        data: Raw audio file bytes
        filename: Original filename (helps with format detection)

    Returns:
        AudioMetadata with format, duration, sample_rate, channels

    Raises:
        InvalidAudioError: If file is not valid audio or unsupported
        AudioProbeError: If probing fails unexpectedly
    """
    try:
        tag = TinyTag.get(file_obj=BytesIO(data), filename=filename)
    except TinyTagException as e:
        raise InvalidAudioError(
            f"Unable to read audio file: {e}. "
            "Please ensure the file is a valid audio format (MP3, WAV, FLAC, OGG, M4A)."
        ) from e
    except Exception as e:
        raise AudioProbeError(f"Unexpected error probing audio: {e}") from e

    # Validate duration
    if tag.duration is None:
        raise InvalidAudioError(
            "Could not determine audio duration. File may be corrupted."
        )

    duration = tag.duration

    if duration < MIN_DURATION_SECONDS:
        raise InvalidAudioError(
            f"Audio too short: {duration:.2f}s. Minimum is {MIN_DURATION_SECONDS}s."
        )

    if duration > MAX_DURATION_SECONDS:
        hours = MAX_DURATION_SECONDS / 3600
        raise InvalidAudioError(
            f"Audio too long: {duration / 3600:.1f} hours. Maximum is {hours:.0f} hours."
        )

    # Get sample rate
    if tag.samplerate is None:
        raise InvalidAudioError(
            "Could not determine sample rate. File may be corrupted."
        )
    sample_rate = tag.samplerate

    # Get channels
    if tag.channels is None:
        raise InvalidAudioError(
            "Could not determine channel count. File may be corrupted."
        )
    channels = tag.channels

    # Get bit depth (may be None for lossy formats like MP3)
    bit_depth = tag.bitdepth

    # Determine format from filename or fallback
    audio_format = "unknown"
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in ("mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "aiff"):
            audio_format = ext

    logger.info(
        "audio_probed",
        format=audio_format,
        duration=duration,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
    )

    return AudioMetadata(
        format=audio_format,
        duration=duration,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
    )
