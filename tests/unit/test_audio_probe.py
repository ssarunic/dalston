"""Unit tests for gateway audio probing."""

from __future__ import annotations

from pathlib import Path

import pytest

from dalston.gateway.services.audio_probe import (
    InvalidAudioError,
    probe_audio,
    sniff_audio_format,
)

AUDIO_DIR = Path(__file__).parents[1] / "audio"
MP3_BYTES = (AUDIO_DIR / "sine_1s.mp3").read_bytes()
WAV_BYTES = (AUDIO_DIR / "test1_speaker1.wav").read_bytes()


def test_mp3_served_with_wav_filename_probes_as_mp3() -> None:
    # Simplecast serves transcoded MP3 (audio/mpeg) with
    # Content-Disposition: filename="<original upload>.wav"
    metadata = probe_audio(MP3_BYTES, "ul-yash-final-simplecast-audio-n-chapter-1.wav")

    assert metadata.format == "mp3"
    assert metadata.duration == pytest.approx(1.0, abs=0.2)  # MP3 frame padding
    assert metadata.sample_rate == 16000
    assert metadata.channels == 1


def test_wav_served_with_mp3_filename_probes_as_wav() -> None:
    metadata = probe_audio(WAV_BYTES, "episode.mp3")

    assert metadata.format == "wav"
    assert metadata.bit_depth is not None


@pytest.mark.parametrize("filename", ["audio.mp3", None, "noextension"])
def test_mp3_probes_regardless_of_filename(filename: str | None) -> None:
    assert probe_audio(MP3_BYTES, filename).format == "mp3"


def test_unrecognised_content_falls_back_to_filename() -> None:
    text_as_wav = (AUDIO_DIR / "invalid" / "text_as_wav.wav").read_bytes()
    assert sniff_audio_format(text_as_wav) is None

    with pytest.raises(InvalidAudioError, match="Invalid WAV header"):
        probe_audio(text_as_wav, "text_as_wav.wav")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00", "mp3"),
        (b"\xff\xfb\x90\x64" + b"\x00" * 8, "mp3"),
        (b"\xff\xf3\x48\xc4" + b"\x00" * 8, "mp3"),
        (b"\xff\xf1\x50\x80" + b"\x00" * 8, "aac"),
        (b"RIFF\x00\x00\x00\x00WAVE", "wav"),
        (b"fLaC" + b"\x00" * 8, "flac"),
        (b"OggS" + b"\x00" * 8, "ogg"),
        (b"\x00\x00\x00\x20ftypM4A ", "m4a"),
        (b"FORM\x00\x00\x00\x00AIFF", "aiff"),
        (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c", "wma"),
        (b"RIFF\x00\x00\x00\x00AVI ", None),
        (b"not audio at all", None),
        (b"", None),
    ],
)
def test_sniff_audio_format(header: bytes, expected: str | None) -> None:
    assert sniff_audio_format(header) == expected
