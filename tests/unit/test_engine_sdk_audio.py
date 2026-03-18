"""Unit tests for engine SDK audio format utilities (M81).

Tests ensure_audio_format() fast path, slow path, and error handling.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from dalston.engine_sdk.audio import (
    SPEECH_STANDARD,
    AudioFormat,
    EngineAudioError,
    _probe_format,
    ensure_audio_format,
)


@pytest.fixture()
def compliant_wav(tmp_path: Path) -> Path:
    """Create a 16kHz, mono, 16-bit PCM WAV file."""
    samples = np.zeros(16000, dtype=np.int16)
    path = tmp_path / "compliant.wav"
    sf.write(str(path), samples, 16000, subtype="PCM_16")
    return path


@pytest.fixture()
def stereo_44k_wav(tmp_path: Path) -> Path:
    """Create a 44.1kHz, stereo, 16-bit PCM WAV file."""
    samples = np.zeros((44100, 2), dtype=np.int16)
    path = tmp_path / "stereo_44k.wav"
    sf.write(str(path), samples, 44100, subtype="PCM_16")
    return path


@pytest.fixture()
def mono_48k_wav(tmp_path: Path) -> Path:
    """Create a 48kHz, mono, 16-bit PCM WAV file."""
    samples = np.zeros(48000, dtype=np.int16)
    path = tmp_path / "mono_48k.wav"
    sf.write(str(path), samples, 48000, subtype="PCM_16")
    return path


class TestAudioFormat:
    """Tests for AudioFormat dataclass."""

    def test_defaults(self):
        fmt = AudioFormat()
        assert fmt.sample_rate == 16000
        assert fmt.channels == 1
        assert fmt.bit_depth == 16

    def test_speech_standard(self):
        assert SPEECH_STANDARD == AudioFormat(16000, 1, 16)

    def test_custom_format(self):
        fmt = AudioFormat(sample_rate=8000, channels=2, bit_depth=24)
        assert fmt.sample_rate == 8000
        assert fmt.channels == 2
        assert fmt.bit_depth == 24

    def test_frozen(self):
        with pytest.raises(AttributeError):
            SPEECH_STANDARD.sample_rate = 44100  # type: ignore[misc]


class TestProbeFormat:
    """Tests for _probe_format()."""

    def test_compliant_wav(self, compliant_wav: Path):
        fmt = _probe_format(compliant_wav)
        assert fmt == SPEECH_STANDARD

    def test_stereo_44k(self, stereo_44k_wav: Path):
        fmt = _probe_format(stereo_44k_wav)
        assert fmt is not None
        assert fmt.sample_rate == 44100
        assert fmt.channels == 2
        assert fmt.bit_depth == 16

    def test_nonexistent_file(self, tmp_path: Path):
        result = _probe_format(tmp_path / "nope.wav")
        assert result is None

    def test_non_audio_file(self, tmp_path: Path):
        path = tmp_path / "readme.txt"
        path.write_text("not audio")
        result = _probe_format(path)
        assert result is None


class TestEnsureAudioFormatFastPath:
    """Tests for the fast path (already compliant, no conversion)."""

    def test_compliant_returns_same_path(self, compliant_wav: Path):
        result = ensure_audio_format(compliant_wav)
        assert result == compliant_wav

    def test_no_subprocess_on_fast_path(self, compliant_wav: Path):
        with patch("dalston.engine_sdk.audio.subprocess.run") as mock_run:
            result = ensure_audio_format(compliant_wav)
            assert result == compliant_wav
            mock_run.assert_not_called()


_has_ffmpeg = shutil.which("ffmpeg") is not None


class TestEnsureAudioFormatSlowPath:
    """Tests for the slow path (conversion via ffmpeg)."""

    @pytest.fixture(autouse=True)
    def _reset_ffmpeg_cache(self):
        """Reset the global ffmpeg availability cache between tests."""
        import dalston.engine_sdk.audio as mod

        original = mod._ffmpeg_available
        mod._ffmpeg_available = None
        yield
        mod._ffmpeg_available = original

    @pytest.mark.skipif(not _has_ffmpeg, reason="ffmpeg not installed")
    def test_non_compliant_converts(self, stereo_44k_wav: Path, tmp_path: Path):
        result = ensure_audio_format(stereo_44k_wav, work_dir=tmp_path)
        assert result != stereo_44k_wav
        assert result.exists()

        # Verify output format
        info = sf.info(str(result))
        assert info.samplerate == 16000
        assert info.channels == 1
        assert info.subtype == "PCM_16"

    @pytest.mark.skipif(not _has_ffmpeg, reason="ffmpeg not installed")
    def test_different_sample_rate_converts(self, mono_48k_wav: Path, tmp_path: Path):
        result = ensure_audio_format(mono_48k_wav, work_dir=tmp_path)
        assert result != mono_48k_wav
        info = sf.info(str(result))
        assert info.samplerate == 16000

    @pytest.mark.skipif(not _has_ffmpeg, reason="ffmpeg not installed")
    def test_custom_target_format(self, compliant_wav: Path, tmp_path: Path):
        target = AudioFormat(sample_rate=8000, channels=1, bit_depth=16)
        result = ensure_audio_format(compliant_wav, target=target, work_dir=tmp_path)
        assert result != compliant_wav
        info = sf.info(str(result))
        assert info.samplerate == 8000

    def test_missing_ffmpeg_raises(self, stereo_44k_wav: Path, tmp_path: Path):
        with patch("dalston.engine_sdk.audio._check_ffmpeg", return_value=False):
            with pytest.raises(EngineAudioError, match="ffmpeg is not installed"):
                ensure_audio_format(stereo_44k_wav, work_dir=tmp_path)

    def test_missing_ffmpeg_compliant_ok(self, compliant_wav: Path):
        """Even without ffmpeg, compliant files take the fast path."""
        with patch("dalston.engine_sdk.audio._check_ffmpeg", return_value=False):
            result = ensure_audio_format(compliant_wav)
            assert result == compliant_wav

    @pytest.mark.skipif(not _has_ffmpeg, reason="ffmpeg not installed")
    def test_work_dir_defaults_to_parent(self, stereo_44k_wav: Path):
        result = ensure_audio_format(stereo_44k_wav)
        assert result.parent == stereo_44k_wav.parent

    def test_ffmpeg_failure_raises(self, stereo_44k_wav: Path, tmp_path: Path):
        with (
            patch("dalston.engine_sdk.audio._check_ffmpeg", return_value=True),
            patch(
                "dalston.engine_sdk.audio.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stderr="boom"
                ),
            ),
        ):
            with pytest.raises(EngineAudioError, match="ffmpeg conversion failed"):
                ensure_audio_format(stereo_44k_wav, work_dir=tmp_path)


class TestEngineBaseAudioFormat:
    """Tests that Engine base class has the audio_format attribute."""

    def test_default_is_speech_standard(self):
        from dalston.engine_sdk.base import Engine

        assert Engine.audio_format == SPEECH_STANDARD

    def test_none_for_non_audio_engines(self):
        from dalston.engine_sdk.base import Engine

        class MergeEngine(Engine):
            audio_format = None

            def process(self, task_request, ctx):
                pass

        assert MergeEngine.audio_format is None
