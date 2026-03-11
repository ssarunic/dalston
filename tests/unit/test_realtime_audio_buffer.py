"""Unit tests for AudioBuffer, focusing on resampling quality."""

import struct

import numpy as np
import pytest

from dalston.realtime_sdk.session import AudioBuffer


class TestAudioBufferResampling:
    """Tests for anti-aliased resampling in AudioBuffer."""

    def test_no_resample_when_rates_match(self):
        """Resampling is a no-op when client and worker rates are equal."""
        buf = AudioBuffer(
            sample_rate=16000, encoding="pcm_s16le", client_sample_rate=16000
        )

        tone = _sine_pcm16(freq_hz=440, sample_rate=16000, duration_s=0.1)
        buf.add(tone)

        expected_samples = len(tone) // 2  # 2 bytes per int16 sample
        assert buf._total_samples == expected_samples

    def test_downsample_preserves_low_frequency(self):
        """A 400 Hz tone survives 48 kHz → 16 kHz downsampling."""
        buf = AudioBuffer(
            sample_rate=16000,
            encoding="pcm_s16le",
            client_sample_rate=48000,
            chunk_duration_ms=100,
        )

        tone = _sine_pcm16(freq_hz=400, sample_rate=48000, duration_s=0.1)
        buf.add(tone)

        # Extract all buffered audio
        chunks = _drain_chunks(buf)
        assert len(chunks) > 0, "Should have at least one chunk after 100 ms"

        audio = np.concatenate(chunks)
        # Verify dominant frequency via FFT
        dominant = _dominant_frequency(audio, sample_rate=16000)
        assert abs(dominant - 400) < 50, f"Expected ~400 Hz, got {dominant} Hz"

    def test_downsample_attenuates_above_nyquist(self):
        """A 9 kHz tone (above 8 kHz Nyquist for 16 kHz) should be attenuated.

        With proper anti-aliasing the energy above Nyquist is filtered out.
        Without it, the tone aliases into the audible band.
        """
        buf = AudioBuffer(
            sample_rate=16000,
            encoding="pcm_s16le",
            client_sample_rate=48000,
            chunk_duration_ms=200,
        )

        # 9 kHz tone at 48 kHz — above 8 kHz Nyquist of 16 kHz target
        tone = _sine_pcm16(freq_hz=9000, sample_rate=48000, duration_s=0.2)
        buf.add(tone)

        chunks = _drain_chunks(buf)
        audio = np.concatenate(chunks)

        # With anti-aliasing, the 9 kHz tone should be heavily attenuated.
        # RMS of the resampled signal should be much smaller than the original.
        rms = np.sqrt(np.mean(audio**2))
        assert rms < 0.05, (
            f"Above-Nyquist tone should be attenuated (RMS={rms:.4f}). "
            "Anti-aliasing filter may not be working."
        )

    def test_upsample_output_length(self):
        """Upsampling 8 kHz → 16 kHz should roughly double sample count."""
        buf = AudioBuffer(
            sample_rate=16000,
            encoding="pcm_s16le",
            client_sample_rate=8000,
            chunk_duration_ms=100,
        )

        # 100 ms at 8 kHz = 800 samples → should become ~1600 at 16 kHz
        tone = _sine_pcm16(freq_hz=400, sample_rate=8000, duration_s=0.1)
        buf.add(tone)

        expected_samples = 1600
        assert abs(buf._total_samples - expected_samples) <= 2

    def test_empty_input_returns_empty(self):
        """Empty byte input should not crash."""
        buf = AudioBuffer(
            sample_rate=16000,
            encoding="pcm_s16le",
            client_sample_rate=48000,
        )

        buf.add(b"")
        assert buf._total_samples == 0


# --- helpers ---


def _sine_pcm16(freq_hz: float, sample_rate: int, duration_s: float) -> bytes:
    """Generate a sine wave as PCM s16le bytes."""
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate
    samples = (np.sin(2 * np.pi * freq_hz * t) * 30000).astype(np.int16)
    return samples.tobytes()


def _drain_chunks(buf: AudioBuffer) -> list[np.ndarray]:
    """Pull all available chunks from the buffer."""
    chunks: list[np.ndarray] = []
    while True:
        chunk = buf.get_chunk()
        if chunk is None:
            break
        chunks.append(chunk)
    return chunks


def _dominant_frequency(audio: np.ndarray, sample_rate: int) -> float:
    """Return the dominant frequency in an audio signal via FFT."""
    spectrum = np.abs(np.fft.rfft(audio))
    freqs = np.fft.rfftfreq(len(audio), d=1.0 / sample_rate)
    return float(freqs[np.argmax(spectrum)])
