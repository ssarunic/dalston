"""Unit tests for M92 review R1: prepare-stage speech-region detection.

audio-prepare runs Silero VAD over the prepared files (union across
channels) so the assembler's missed-speech coverage check has ground
truth. Detection degrades gracefully when the VAD stack is unavailable.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog


@pytest.fixture(scope="module")
def prepare_module():
    engine_path = Path("engines/stt-prepare/audio-prepare/engine.py")
    if not engine_path.exists():
        pytest.skip("audio-prepare engine not found")
    spec = importlib.util.spec_from_file_location("m92_prep_vad_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m92_prep_vad_engine"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("m92_prep_vad_engine", None)


class TestMergeIntervals:
    def test_union_across_channels(self, prepare_module):
        merged = prepare_module._merge_intervals(
            [(0.0, 3.5), (34.0, 37.5), (2.0, 5.0), (48.0, 56.0)]
        )
        assert merged == [(0.0, 5.0), (34.0, 37.5), (48.0, 56.0)]

    def test_touching_intervals_merge(self, prepare_module):
        assert prepare_module._merge_intervals([(0.0, 1.0), (1.0, 2.0)]) == [(0.0, 2.0)]

    def test_empty_and_degenerate(self, prepare_module):
        assert prepare_module._merge_intervals([]) == []
        assert prepare_module._merge_intervals([(2.0, 2.0), (3.0, 1.0)]) == []


def _bare_engine(cls) -> SimpleNamespace:
    return SimpleNamespace(
        logger=structlog.get_logger(),
        _detect_speech_regions=cls._detect_speech_regions,
    )


class _FakeChunker:
    """Per-path canned VAD segments keyed by filename."""

    RESULTS = {
        "prepared_ch0.wav": [(3.0, 16.0), (21.0, 24.0)],
        "prepared_ch1.wav": [(0.0, 3.5), (34.0, 37.5), (48.0, 56.0)],
    }

    def detect_speech(self, path):
        return [
            SimpleNamespace(start=s, end=e)
            for s, e in self.RESULTS.get(Path(path).name, [])
        ]


class TestDetectSpeechRegions:
    def test_union_and_ratio(self, prepare_module, monkeypatch):
        import dalston.engine_sdk.vad as vad_module

        monkeypatch.setattr(vad_module, "VadChunker", _FakeChunker)
        engine = _bare_engine(prepare_module.AudioPrepareEngine)

        regions, ratio = engine._detect_speech_regions(
            engine,
            [Path("/tmp/prepared_ch0.wav"), Path("/tmp/prepared_ch1.wav")],
            56.0,
        )
        assert regions is not None
        spans = [(r.start, r.end) for r in regions]
        assert spans == [(0.0, 16.0), (21.0, 24.0), (34.0, 37.5), (48.0, 56.0)]
        # speech total = 16 + 3 + 3.5 + 8 = 30.5s of 56s
        assert ratio == pytest.approx(30.5 / 56.0, abs=0.001)

    def test_unavailable_stack_degrades_to_none(self, prepare_module, monkeypatch):
        import dalston.engine_sdk.vad as vad_module

        class _Broken:
            def __init__(self):
                raise RuntimeError("no onnxruntime in this container")

        monkeypatch.setattr(vad_module, "VadChunker", _Broken)
        engine = _bare_engine(prepare_module.AudioPrepareEngine)

        regions, ratio = engine._detect_speech_regions(
            engine, [Path("/tmp/prepared.wav")], 30.0
        )
        assert regions is None
        assert ratio is None

    def test_no_speech_yields_empty_regions_not_none(self, prepare_module, monkeypatch):
        import dalston.engine_sdk.vad as vad_module

        class _Silent:
            def detect_speech(self, path):
                return []

        monkeypatch.setattr(vad_module, "VadChunker", _Silent)
        engine = _bare_engine(prepare_module.AudioPrepareEngine)

        regions, ratio = engine._detect_speech_regions(
            engine, [Path("/tmp/prepared.wav")], 30.0
        )
        # Detection ran and found nothing — that is data, not absence.
        assert regions == []
        assert ratio == 0.0
