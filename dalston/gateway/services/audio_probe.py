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


_KNOWN_EXTENSIONS = ("mp3", "wav", "flac", "ogg", "m4a", "aac", "wma", "aiff")

_WMA_HEADER = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c"


def sniff_audio_format(data: bytes) -> str | None:
    """Detect the container format from magic bytes, or None if unrecognised.

    Content beats filename: hosts like Simplecast serve transcoded MP3 with a
    Content-Disposition naming the creator's original ``.wav`` upload.
    """
    header = data[:12]
    if header.startswith(b"ID3"):
        return "mp3"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav"
    if header.startswith(b"fLaC"):
        return "flac"
    if header.startswith(b"OggS"):
        return "ogg"
    if header[4:8] == b"ftyp":
        return "m4a"
    if header.startswith(b"FORM") and header[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if data[:16] == _WMA_HEADER:
        return "wma"
    if len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0:
        # MPEG frame sync: layer bits 00 mean ADTS AAC, anything else is MP1/2/3
        return "aac" if header[1] & 0x06 == 0 else "mp3"
    return None


def _filename_format(filename: str | None) -> str | None:
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in _KNOWN_EXTENSIONS:
            return ext
    return None


def probe_audio(data: bytes, filename: str | None = None) -> AudioMetadata:
    """Probe audio data to extract metadata.

    Args:
        data: Raw audio file bytes
        filename: Original filename (fallback when content sniffing fails)

    Returns:
        AudioMetadata with format, duration, sample_rate, channels

    Raises:
        InvalidAudioError: If file is not valid audio or unsupported
        AudioProbeError: If probing fails unexpectedly
    """
    sniffed_format = sniff_audio_format(data)
    # TinyTag picks its parser from the filename extension before looking at
    # content, so hand it a name matching the sniffed format.
    probe_filename = f"audio.{sniffed_format}" if sniffed_format else filename

    try:
        tag = TinyTag.get(file_obj=BytesIO(data), filename=probe_filename)
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

    audio_format = sniffed_format or _filename_format(filename) or "unknown"

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
