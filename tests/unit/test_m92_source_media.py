"""Unit tests for M92 step 92.5: original media metadata in responses.

The response's audio_channels/sample_rate/audio_duration must describe the
uploaded file, not the resampled mono channel files prepare produced.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dalston.common.pipeline_types import AudioMedia, PreparationResponse
from dalston.common.transcript import (
    _extract_audio_metadata,
    assemble_per_channel_transcript,
)

_SOURCE_MEDIA = {
    "artifact_id": "job:source:audio",
    "format": "wav",
    "duration": 56.2,
    "sample_rate": 8000,
    "channels": 2,
}

_SPLIT_CHANNEL_FILES = [
    {
        "artifact_id": f"t:prepared_audio_ch{i}",
        "format": "wav",
        "duration": 56.2,
        "sample_rate": 16000,
        "channels": 1,
    }
    for i in range(2)
]


class TestPreparationResponseSchema:
    def test_source_media_accepted(self):
        resp = PreparationResponse(
            channel_files=[AudioMedia.model_validate(_SPLIT_CHANNEL_FILES[0])],
            source_media=AudioMedia.model_validate(_SOURCE_MEDIA),
            engine_id="audio-prepare",
        )
        assert resp.source_media is not None
        assert resp.source_media.channels == 2

    def test_source_media_optional(self):
        resp = PreparationResponse(
            channel_files=[AudioMedia.model_validate(_SPLIT_CHANNEL_FILES[0])],
            engine_id="audio-prepare",
        )
        assert resp.source_media is None


class TestExtractAudioMetadataSourceMedia:
    def test_prefers_source_media(self):
        prepare = {
            "channel_files": _SPLIT_CHANNEL_FILES,
            "source_media": _SOURCE_MEDIA,
            "split_channels": True,
        }
        duration, channels, sample_rate = _extract_audio_metadata(prepare)
        assert (duration, channels, sample_rate) == (56.2, 2, 8000)

    def test_legacy_split_falls_back_to_file_count(self):
        prepare = {"channel_files": _SPLIT_CHANNEL_FILES, "split_channels": True}
        _, channels, sample_rate = _extract_audio_metadata(prepare)
        assert channels == 2  # number of split files, not the mono probe
        assert sample_rate == 16000  # legacy: post-resample value survives

    def test_legacy_mono_unchanged(self):
        prepare = {
            "channel_files": [_SPLIT_CHANNEL_FILES[0]],
            "split_channels": False,
        }
        _, channels, sample_rate = _extract_audio_metadata(prepare)
        assert channels == 1
        assert sample_rate == 16000

    def test_per_channel_assembly_reports_original(self):
        stage_outputs = {
            "prepare": {
                "channel_files": _SPLIT_CHANNEL_FILES,
                "source_media": _SOURCE_MEDIA,
                "split_channels": True,
            },
            "transcribe_ch0": {
                "text": "A",
                "language": "hr",
                "segments": [{"start": 0.0, "end": 2.0, "text": "A"}],
                "engine_id": "nemo",
            },
            "transcribe_ch1": {
                "text": "B",
                "language": "hr",
                "segments": [{"start": 3.0, "end": 4.0, "text": "B"}],
                "engine_id": "nemo",
            },
        }
        result = assemble_per_channel_transcript(
            job_id="j1", stage_outputs=stage_outputs, channel_count=2
        )
        assert result.metadata.audio_channels == 2
        assert result.metadata.sample_rate == 8000
        assert result.metadata.audio_duration == 56.2


@pytest.fixture(scope="module")
def prepare_engine_cls():
    engine_path = Path("engines/stt-prepare/audio-prepare/engine.py")
    if not engine_path.exists():
        pytest.skip("audio-prepare engine not found")
    spec = importlib.util.spec_from_file_location("m92_prepare_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m92_prepare_engine"] = module
    try:
        spec.loader.exec_module(module)
        yield module.AudioPrepareEngine
    finally:
        sys.modules.pop("m92_prepare_engine", None)


class TestBuildSourceMedia:
    _PROBE = {
        "duration": 56.2,
        "sample_rate": 8000,
        "channels": 2,
        "bit_depth": 16,
        "codec_name": "pcm_s16le",
    }

    def test_uses_request_media_identity_and_probe_numbers(self, prepare_engine_cls):
        task_request = SimpleNamespace(
            media={
                "artifact_id": "job-1:source:audio",
                "format": "mp3",
                # Gateway probe numbers are deliberately wrong here — the
                # engine's own ffprobe must win for numeric properties.
                "sample_rate": 44100,
                "channels": 1,
                "duration": 1.0,
            }
        )
        media = prepare_engine_cls._build_source_media(task_request, self._PROBE)
        assert media.artifact_id == "job-1:source:audio"
        assert media.format == "mp3"
        assert media.sample_rate == 8000
        assert media.channels == 2
        assert media.duration == 56.2
        assert media.bit_depth == 16

    def test_falls_back_without_request_media(self, prepare_engine_cls):
        task_request = SimpleNamespace(media=None)
        media = prepare_engine_cls._build_source_media(task_request, self._PROBE)
        assert media.artifact_id == "source:audio"
        assert media.format == "pcm_s16le"
        assert media.channels == 2
