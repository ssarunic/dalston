"""Unit tests for M92 step 92.7: speech coverage + honest segment bounds.

Segment ends clamp to recognized content instead of VAD/hypothesis
boundaries; missed prepare-detected speech produces a pipeline warning;
empty VAD results say so; the Silero threshold is env-tunable.
"""

from types import SimpleNamespace

from dalston.common.audio_defaults import DEFAULT_VAD_THRESHOLD, get_vad_threshold
from dalston.common.transcript import (
    _append_missed_speech_warning,
    _compute_missed_speech,
    assemble_per_channel_transcript,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.inference.nemo_inference import NemoInference

# The incident shape: ch1 spoke at 0-3.5s, 34-37.5s, 48-56s; only the first
# turn was transcribed, inside a hypothesis span inflated to 25.92s.
_INCIDENT_REGIONS = [
    {"start": 0.0, "end": 3.5},
    {"start": 34.0, "end": 37.5},
    {"start": 48.0, "end": 56.0},
]


def _seg(start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end)


class TestSegmentEndClamping:
    def test_nemo_segment_end_clamps_to_last_word(self):
        hyp = SimpleNamespace(
            text="deset poljskih rijeci",
            timestamp={
                "word": [
                    {"word": "deset", "start": 0.3, "end": 1.0},
                    {"word": "poljskih", "start": 1.1, "end": 2.2},
                    {"word": "rijeci", "start": 2.4, "end": 3.5},
                ],
                "segment": [
                    {"start": 0.24, "end": 25.92, "segment": "deset poljskih rijeci"}
                ],
            },
        )
        segments, _ = NemoInference._parse_hypothesis(hyp, hyp.text)
        assert segments[0].end == 3.5  # not the fabricated 25.92

    def test_nemo_segment_without_words_keeps_span(self):
        hyp = SimpleNamespace(
            text="x",
            timestamp={
                "word": [],
                "segment": [{"start": 0.0, "end": 5.0, "segment": "x"}],
            },
        )
        segments, _ = NemoInference._parse_hypothesis(hyp, hyp.text)
        assert segments[0].end == 5.0


class TestComputeMissedSpeech:
    def test_incident_shape_detects_dropped_turns(self):
        missed = _compute_missed_speech(_INCIDENT_REGIONS, [_seg(0.24, 3.5)])
        total = sum(end - start for start, end in missed)
        assert abs(total - 11.5) < 0.01
        assert missed == [(34.0, 37.5), (48.0, 56.0)]

    def test_fully_covered_regions_yield_nothing(self):
        missed = _compute_missed_speech(
            _INCIDENT_REGIONS,
            [_seg(0.0, 3.5), _seg(34.0, 37.5), _seg(48.0, 56.0)],
        )
        assert missed == []

    def test_tolerance_absorbs_boundary_slack(self):
        # Segment ends 0.9s before the region does — within the 1s slack.
        missed = _compute_missed_speech([{"start": 0.0, "end": 5.0}], [_seg(0.0, 4.1)])
        assert missed == []

    def test_slivers_below_min_region_ignored(self):
        missed = _compute_missed_speech([{"start": 0.0, "end": 5.2}], [_seg(0.0, 4.0)])
        assert missed == []  # 0.2s residual < 0.25s minimum


class TestMissedSpeechWarning:
    def test_incident_fires_warning_with_spans(self):
        warnings: list = []
        _append_missed_speech_warning(
            {"speech_regions": _INCIDENT_REGIONS}, [_seg(0.24, 3.5)], warnings
        )
        assert len(warnings) == 1
        assert "11.5s of detected speech was not transcribed" in warnings[0]
        assert "34.0-37.5s" in warnings[0]
        assert "48.0-56.0s" in warnings[0]

    def test_full_coverage_is_silent(self):
        warnings: list = []
        _append_missed_speech_warning(
            {"speech_regions": _INCIDENT_REGIONS},
            [_seg(0.0, 3.5), _seg(34.0, 37.5), _seg(48.0, 56.0)],
            warnings,
        )
        assert warnings == []

    def test_small_miss_below_thresholds_is_silent(self):
        # 2s missed: below the 3s absolute floor.
        warnings: list = []
        _append_missed_speech_warning(
            {
                "speech_regions": [
                    {"start": 0.0, "end": 30.0},
                    {"start": 40.0, "end": 42.0},
                ]
            },
            [_seg(0.0, 30.0)],
            warnings,
        )
        assert warnings == []

    def test_no_regions_is_silent(self):
        warnings: list = []
        _append_missed_speech_warning({}, [_seg(0.0, 1.0)], warnings)
        _append_missed_speech_warning(None, [_seg(0.0, 1.0)], warnings)
        assert warnings == []

    def test_per_channel_assembly_integration(self):
        stage_outputs = {
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "a1",
                        "format": "wav",
                        "duration": 56.2,
                        "sample_rate": 16000,
                        "channels": 1,
                    }
                ],
                "speech_regions": _INCIDENT_REGIONS,
                "split_channels": True,
            },
            "transcribe_ch0": {
                "text": "halo",
                "language": "hr",
                "segments": [{"start": 0.24, "end": 3.5, "text": "halo"}],
                "engine_id": "nemo",
            },
            "transcribe_ch1": {
                "text": "",
                "language": "hr",
                "segments": [],
                "engine_id": "nemo",
            },
        }
        result = assemble_per_channel_transcript(
            job_id="j1", stage_outputs=stage_outputs, channel_count=2
        )
        assert any(
            "detected speech was not transcribed" in w
            for w in result.metadata.pipeline_warnings
        )


class TestEmptyMergeWarning:
    def test_empty_chunk_merge_warns(self):
        fake_engine = SimpleNamespace(
            build_transcript=BaseBatchTranscribeEngine.build_transcript,
            engine_id="nemo",
        )
        transcript = BaseBatchTranscribeEngine._merge_chunk_transcripts(fake_engine, [])
        assert transcript.text == ""
        assert any("No speech detected by VAD" in w for w in transcript.warnings)


class TestVadThresholdKnob:
    def test_default_without_env(self, monkeypatch):
        monkeypatch.delenv("DALSTON_VAD_THRESHOLD", raising=False)
        assert get_vad_threshold() == DEFAULT_VAD_THRESHOLD

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DALSTON_VAD_THRESHOLD", "0.3")
        assert get_vad_threshold() == 0.3

    def test_invalid_values_fall_back(self, monkeypatch):
        for bad in ("abc", "0", "1", "-0.2", "1.5"):
            monkeypatch.setenv("DALSTON_VAD_THRESHOLD", bad)
            assert get_vad_threshold() == DEFAULT_VAD_THRESHOLD

    def test_vad_chunker_picks_up_env(self, monkeypatch):
        from dalston.engine_sdk.vad import VadChunker

        monkeypatch.setenv("DALSTON_VAD_THRESHOLD", "0.3")
        assert VadChunker().vad_threshold == 0.3
        # Explicit argument still wins.
        assert VadChunker(vad_threshold=0.7).vad_threshold == 0.7
