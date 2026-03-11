"""Audio helpers shared by vLLM-ASR engines.

Realtime workers produce in-memory numpy audio. vLLM's current multimodal
audio chat path consumes file URLs, so we normalize numpy buffers and write
temporary WAV files as the bridge format.
"""

from __future__ import annotations

import tempfile
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np


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
