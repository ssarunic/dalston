"""Unit tests for VadChunker.

Avoids loading the real Silero VAD model by mocking ``_ensure_model`` and
injecting a fake ``_get_speech_timestamps`` callable. Audio is synthesised
as a silent float32 array so ``soundfile.write`` can round-trip it.
"""

from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from dalston.engine_sdk.vad import AudioChunk, SpeechSegment, VadChunker

SAMPLE_RATE = 16000


def _write_silent_wav(path: Path, duration_s: float) -> None:
    """Write a 16 kHz mono silent WAV of the given duration."""
    n_samples = int(duration_s * SAMPLE_RATE)
    pcm = np.zeros(n_samples, dtype=np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


def _fake_speech_timestamps(
    segments_in_seconds: list[tuple[float, float]],
):
    """Build a fake get_speech_timestamps that returns pre-canned samples."""

    def _impl(audio, model, **kwargs):  # noqa: ARG001
        return [
            {
                "start": int(s * SAMPLE_RATE),
                "end": int(e * SAMPLE_RATE),
            }
            for s, e in segments_in_seconds
        ]

    return _impl


def _install_fake_vad(
    chunker: VadChunker,
    segments_in_seconds: list[tuple[float, float]],
) -> None:
    """Bypass real model loading and inject a fake VAD callable."""
    chunker._model = MagicMock(name="fake_silero_model")
    chunker._get_speech_timestamps = _fake_speech_timestamps(segments_in_seconds)
    # Monkey-patch _ensure_model so it's a no-op
    chunker._ensure_model = lambda: None  # type: ignore[method-assign]


class TestDetectSpeech:
    def test_empty_audio_returns_empty(self, tmp_path: Path) -> None:
        audio = tmp_path / "silent.wav"
        _write_silent_wav(audio, duration_s=5.0)

        chunker = VadChunker(max_chunk_duration_s=60.0)
        _install_fake_vad(chunker, [])

        assert chunker.detect_speech(audio) == []

    def test_maps_samples_to_seconds(self, tmp_path: Path) -> None:
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=10.0)

        chunker = VadChunker(max_chunk_duration_s=60.0)
        _install_fake_vad(chunker, [(0.5, 2.5), (3.0, 9.75)])

        segs = chunker.detect_speech(audio)
        assert len(segs) == 2
        assert segs[0] == SpeechSegment(start=0.5, end=2.5)
        assert segs[1] == SpeechSegment(start=3.0, end=9.75)


class TestSplit:
    def test_no_speech_returns_empty(self, tmp_path: Path) -> None:
        audio = tmp_path / "silent.wav"
        _write_silent_wav(audio, duration_s=5.0)

        chunker = VadChunker(max_chunk_duration_s=60.0)
        _install_fake_vad(chunker, [])

        chunks = chunker.split(audio, tmp_path / "chunks")
        assert chunks == []

    def test_single_short_span_produces_one_chunk(self, tmp_path: Path) -> None:
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=30.0)

        chunker = VadChunker(max_chunk_duration_s=60.0)
        _install_fake_vad(chunker, [(1.0, 25.0)])

        chunks = chunker.split(audio, tmp_path / "chunks")
        assert len(chunks) == 1
        assert chunks[0].offset == pytest.approx(1.0, abs=1e-3)
        assert chunks[0].duration == pytest.approx(24.0, abs=1e-3)
        assert chunks[0].audio_path.exists()

    def test_multiple_spans_grouped_within_cap(self, tmp_path: Path) -> None:
        """Spans within max_chunk_duration_s of each other go into one chunk."""
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=60.0)

        chunker = VadChunker(max_chunk_duration_s=30.0)
        # Three speech spans: [5-15], [16-25], [28-55]
        # First two fit in one chunk (span 5→25 = 20s ≤ 30s)
        # Third doesn't fit with the first two (span 5→55 = 50s > 30s)
        #   and doesn't fit alone at 28→55 = 27s ≤ 30s
        _install_fake_vad(chunker, [(5.0, 15.0), (16.0, 25.0), (28.0, 55.0)])

        chunks = chunker.split(audio, tmp_path / "chunks")
        assert len(chunks) == 2
        assert chunks[0].offset == pytest.approx(5.0, abs=1e-3)
        assert chunks[0].duration == pytest.approx(20.0, abs=1e-3)
        assert chunks[1].offset == pytest.approx(28.0, abs=1e-3)
        assert chunks[1].duration == pytest.approx(27.0, abs=1e-3)

    def test_force_split_on_overlong_span(self, tmp_path: Path) -> None:
        """A single VAD span longer than the cap is force-split at the boundary."""
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=120.0)

        chunker = VadChunker(max_chunk_duration_s=30.0)
        # One continuous speech span of 70s — must be force-split into 3 chunks
        # of 30, 30, 10
        _install_fake_vad(chunker, [(0.0, 70.0)])

        chunks = chunker.split(audio, tmp_path / "chunks")
        assert len(chunks) == 3
        assert chunks[0].offset == pytest.approx(0.0, abs=1e-3)
        assert chunks[0].duration == pytest.approx(30.0, abs=1e-3)
        assert chunks[1].offset == pytest.approx(30.0, abs=1e-3)
        assert chunks[1].duration == pytest.approx(30.0, abs=1e-3)
        assert chunks[2].offset == pytest.approx(60.0, abs=1e-3)
        assert chunks[2].duration == pytest.approx(10.0, abs=1e-3)
        for c in chunks:
            assert c.duration <= 30.0 + 1e-3

    def test_chunks_respect_hard_limit(self, tmp_path: Path) -> None:
        """Invariant: every chunk's duration <= max_chunk_duration_s."""
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=600.0)

        chunker = VadChunker(max_chunk_duration_s=120.0)
        # Adversarial: many small spans and one medium one
        segments = [(t, t + 5.0) for t in range(0, 500, 10)]
        _install_fake_vad(chunker, segments)

        chunks = chunker.split(audio, tmp_path / "chunks")
        assert len(chunks) > 0
        for c in chunks:
            assert c.duration <= 120.0 + 1e-3

    def test_chunks_cover_all_speech_offsets(self, tmp_path: Path) -> None:
        """Every speech span start should land inside some returned chunk."""
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=200.0)

        chunker = VadChunker(max_chunk_duration_s=60.0)
        speech = [(10.0, 20.0), (25.0, 55.0), (70.0, 85.0), (120.0, 180.0)]
        _install_fake_vad(chunker, speech)

        chunks = chunker.split(audio, tmp_path / "chunks")

        def _covered(t: float) -> bool:
            return any(c.offset <= t <= c.offset + c.duration for c in chunks)

        for s, _ in speech:
            assert _covered(s), f"speech start {s} not covered by any chunk"

    def test_start_offset_drops_chunks_fully_before(self, tmp_path: Path) -> None:
        """Groups ending at or before ``start_offset_s`` are skipped entirely."""
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=60.0)

        chunker = VadChunker(max_chunk_duration_s=30.0)
        _install_fake_vad(chunker, [(5.0, 10.0), (40.0, 50.0)])

        chunks = chunker.split(audio, tmp_path / "chunks", start_offset_s=20.0)

        assert len(chunks) == 1
        assert chunks[0].offset == pytest.approx(40.0, abs=1e-3)
        assert chunks[0].duration == pytest.approx(10.0, abs=1e-3)

    def test_start_offset_trims_straddling_chunk(self, tmp_path: Path) -> None:
        """A group that starts before ``start_offset_s`` but ends after it
        is trimmed — its offset becomes the boundary and its audio slice
        starts there, so unprocessed audio past the boundary is preserved
        without reprocessing the head.
        """
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=60.0)

        chunker = VadChunker(max_chunk_duration_s=30.0)
        # Two speech spans grouped into one chunk spanning [5, 25]:
        _install_fake_vad(chunker, [(5.0, 10.0), (20.0, 25.0), (40.0, 50.0)])

        chunks = chunker.split(audio, tmp_path / "chunks", start_offset_s=15.0)

        # First group [5, 25] straddles boundary 15 → trimmed to [15, 25].
        # Second group [40, 50] starts well past the boundary → unchanged.
        assert len(chunks) == 2
        assert chunks[0].offset == pytest.approx(15.0, abs=1e-3)
        assert chunks[0].duration == pytest.approx(10.0, abs=1e-3)
        assert chunks[1].offset == pytest.approx(40.0, abs=1e-3)
        assert chunks[1].duration == pytest.approx(10.0, abs=1e-3)

    def test_start_offset_past_all_speech_returns_empty(self, tmp_path: Path) -> None:
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=60.0)

        chunker = VadChunker(max_chunk_duration_s=30.0)
        _install_fake_vad(chunker, [(5.0, 10.0), (20.0, 25.0)])

        chunks = chunker.split(audio, tmp_path / "chunks", start_offset_s=30.0)
        assert chunks == []

    def test_chunk_files_written_and_distinct(self, tmp_path: Path) -> None:
        audio = tmp_path / "speech.wav"
        _write_silent_wav(audio, duration_s=200.0)

        chunker = VadChunker(max_chunk_duration_s=60.0)
        _install_fake_vad(chunker, [(0.0, 50.0), (60.0, 100.0), (110.0, 170.0)])

        chunks = chunker.split(audio, tmp_path / "chunks")
        assert len(chunks) >= 2
        paths = {c.audio_path for c in chunks}
        assert len(paths) == len(chunks), "chunk paths should be unique"
        for c in chunks:
            assert c.audio_path.exists()
            assert c.audio_path.stat().st_size > 44  # WAV header is 44 bytes


class TestAudioChunkDataclass:
    def test_fields(self) -> None:
        chunk = AudioChunk(audio_path=Path("/tmp/x.wav"), offset=1.5, duration=30.0)
        assert chunk.offset == 1.5
        assert chunk.duration == 30.0

    def test_speech_segment_duration(self) -> None:
        seg = SpeechSegment(start=2.0, end=7.5)
        assert seg.duration == pytest.approx(5.5)


class TestLoaderResolutionOrder:
    """Verify the loader prefers silero_vad pkg > ONNX env > torch path > hub.

    This exercises the priority order without actually loading any model —
    each _try_* hook is stubbed to return True or False to simulate its
    availability and assert the method dispatches correctly.
    """

    def _build(self) -> VadChunker:
        return VadChunker(max_chunk_duration_s=60.0)

    def test_prefers_silero_package_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunker = self._build()
        calls: list[str] = []

        def _pkg() -> bool:
            calls.append("pkg")
            return True

        def _onnx() -> bool:
            calls.append("onnx")
            return True

        def _path() -> bool:
            calls.append("path")
            return True

        def _hub() -> None:
            calls.append("hub")

        monkeypatch.setattr(chunker, "_try_load_silero_package", _pkg)
        monkeypatch.setattr(chunker, "_try_load_onnx_env", _onnx)
        monkeypatch.setattr(chunker, "_try_load_torch_path_env", _path)
        monkeypatch.setattr(chunker, "_load_from_torch_hub", _hub)

        chunker._ensure_model()

        assert calls == ["pkg"]  # stop at first success

    def test_falls_through_to_onnx_when_package_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunker = self._build()
        calls: list[str] = []

        monkeypatch.setattr(
            chunker,
            "_try_load_silero_package",
            lambda: (calls.append("pkg"), False)[1],
        )
        monkeypatch.setattr(
            chunker,
            "_try_load_onnx_env",
            lambda: (calls.append("onnx"), True)[1],
        )
        monkeypatch.setattr(
            chunker,
            "_try_load_torch_path_env",
            lambda: (calls.append("path"), True)[1],
        )
        monkeypatch.setattr(
            chunker,
            "_load_from_torch_hub",
            lambda: calls.append("hub"),
        )

        chunker._ensure_model()

        assert calls == ["pkg", "onnx"]

    def test_torch_hub_is_last_resort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chunker = self._build()
        calls: list[str] = []

        monkeypatch.setattr(
            chunker,
            "_try_load_silero_package",
            lambda: (calls.append("pkg"), False)[1],
        )
        monkeypatch.setattr(
            chunker,
            "_try_load_onnx_env",
            lambda: (calls.append("onnx"), False)[1],
        )
        monkeypatch.setattr(
            chunker,
            "_try_load_torch_path_env",
            lambda: (calls.append("path"), False)[1],
        )

        def _hub() -> None:
            calls.append("hub")
            chunker._model = object()  # mark as loaded

        monkeypatch.setattr(chunker, "_load_from_torch_hub", _hub)

        chunker._ensure_model()

        assert calls == ["pkg", "onnx", "path", "hub"]

    def test_onnx_env_missing_file_logs_and_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunker = self._build()
        monkeypatch.setenv(
            "DALSTON_SILERO_VAD_ONNX", str(tmp_path / "does-not-exist.onnx")
        )
        assert chunker._try_load_onnx_env() is False

    def test_onnx_env_unset_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunker = self._build()
        monkeypatch.delenv("DALSTON_SILERO_VAD_ONNX", raising=False)
        assert chunker._try_load_onnx_env() is False
