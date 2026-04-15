"""Audio format utilities for batch engines.

Provides ``ensure_audio_format()`` which guarantees that an audio file
matches a declared target format before engine processing begins.

In a normal pipeline the prepare stage already converts to the standard
format, so this utility is a cheap no-op (header check only).  When
engines run standalone or receive unexpected input, the slow path
converts via ffmpeg.

Also provides the shared numpy→PCM16 WAV helpers used by several
engines (vLLM-ASR bridges its realtime numpy buffers into vLLM's
file-URL multimodal input; the batch VAD chunker writes VAD-bounded
chunks back to disk for per-chunk transcribe calls).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import structlog

logger = structlog.get_logger()

# Lazy sentinel — checked once on first slow-path invocation.
_ffmpeg_available: bool | None = None


def _check_ffmpeg() -> bool:
    """Return True if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


@dataclass(frozen=True)
class AudioFormat:
    """Declares an engine's audio input requirements."""

    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16


SPEECH_STANDARD = AudioFormat()  # 16kHz, mono, 16-bit — the common case


class EngineAudioError(Exception):
    """Raised when audio cannot be prepared for engine consumption."""


def _probe_format(audio_path: Path) -> AudioFormat | None:
    """Read WAV/FLAC/OGG header and return the detected format.

    Returns None for formats that soundfile cannot read (MP3, M4A, etc.),
    signalling that conversion is always required.
    """
    try:
        import soundfile as sf

        info = sf.info(str(audio_path))
    except Exception:
        return None

    # Map soundfile subtype strings to bit depth
    subtype_to_bits: dict[str, int] = {
        "PCM_16": 16,
        "PCM_24": 24,
        "PCM_32": 32,
        "PCM_S8": 8,
        "PCM_U8": 8,
        "FLOAT": 32,
        "DOUBLE": 64,
    }
    bit_depth = subtype_to_bits.get(info.subtype)
    if bit_depth is None:
        # Lossy codecs (MP3, OGG vorbis, etc.) don't have a meaningful
        # bit depth.  Return None so the caller always converts them.
        return None

    return AudioFormat(
        sample_rate=info.samplerate,
        channels=info.channels,
        bit_depth=bit_depth,
    )


def ensure_audio_format(
    audio_path: Path,
    target: AudioFormat = SPEECH_STANDARD,
    work_dir: Path | None = None,
) -> Path:
    """Ensure audio file matches the target format.

    If the file is already compliant, returns the original path (no copy,
    no conversion).  Otherwise, converts via ffmpeg into *work_dir* and
    returns the path to the converted file.

    Args:
        audio_path: Path to the input audio file.
        target: Desired output format.
        work_dir: Directory for converted output.  Defaults to the
            parent directory of *audio_path*.

    Returns:
        Path to a file guaranteed to match *target*.

    Raises:
        EngineAudioError: If ffmpeg is unavailable and conversion is needed,
            or if conversion itself fails.
    """
    detected = _probe_format(audio_path)

    if detected is not None and detected == target:
        logger.debug(
            "audio.ensure_format.fast_path",
            path=str(audio_path),
        )
        return audio_path

    # Slow path — need ffmpeg
    global _ffmpeg_available  # noqa: PLW0603
    if _ffmpeg_available is None:
        _ffmpeg_available = _check_ffmpeg()

    if not _ffmpeg_available:
        raise EngineAudioError(
            f"Audio at {audio_path} requires conversion to "
            f"{target.sample_rate}Hz/{target.channels}ch/{target.bit_depth}bit "
            "but ffmpeg is not installed."
        )

    if work_dir is None:
        work_dir = audio_path.parent

    # Deterministic output name based on input filename
    name_hash = hashlib.md5(audio_path.name.encode()).hexdigest()[:8]
    output_path = work_dir / f"prepared_{name_hash}.wav"

    # Map bit_depth to ffmpeg sample_fmt
    bit_depth_to_fmt: dict[int, str] = {
        8: "u8",
        16: "s16",
        24: "s24",
        32: "s32",
    }
    sample_fmt = bit_depth_to_fmt.get(target.bit_depth, "s16")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-ar",
        str(target.sample_rate),
        "-ac",
        str(target.channels),
        "-sample_fmt",
        sample_fmt,
        "-f",
        "wav",
        str(output_path),
    ]

    logger.info(
        "audio.ensure_format.converting",
        input=str(audio_path),
        target_sr=target.sample_rate,
        target_ch=target.channels,
        target_bits=target.bit_depth,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise EngineAudioError(
            f"ffmpeg conversion timed out (600s) for {audio_path}"
        ) from None

    if result.returncode != 0:
        raise EngineAudioError(
            f"ffmpeg conversion failed for {audio_path}: {result.stderr}"
        )

    if not output_path.exists():
        raise EngineAudioError(f"ffmpeg did not produce output file: {output_path}")

    return output_path


# ---------------------------------------------------------------------------
# Numpy -> PCM16 WAV helpers
# ---------------------------------------------------------------------------


def normalize_mono_audio(audio: np.ndarray) -> np.ndarray:
    """Return clipped mono float32 audio in range [-1, 1]."""
    samples = np.asarray(audio)
    if samples.ndim == 0:
        raise ValueError("Audio must be a 1D mono numpy array")

    if samples.ndim > 1:
        samples = np.squeeze(samples)
        if samples.ndim != 1:
            raise ValueError("Audio must be mono")

    if samples.dtype != np.float32:
        samples = samples.astype(np.float32)

    return np.clip(samples, -1.0, 1.0)


def write_wav_file(path: Path, audio: np.ndarray, sample_rate: int = 16000) -> None:
    """Write mono PCM16 WAV to ``path`` from numpy audio samples."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")

    samples = normalize_mono_audio(audio)
    pcm16 = (samples * 32767.0).astype(np.int16)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # PCM16
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())


@contextmanager
def temporary_wav_file(
    audio: np.ndarray,
    sample_rate: int = 16000,
) -> Iterator[Path]:
    """Create a temporary WAV file from numpy audio and clean it up."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        write_wav_file(tmp_path, audio=audio, sample_rate=sample_rate)
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)
