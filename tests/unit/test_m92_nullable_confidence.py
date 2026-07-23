"""Unit tests for M92 step 92.4: nullable language_confidence.

language_confidence is only present when an engine actually computed it
(faster-whisper LID probability); everywhere else it must be null, never
a fabricated 1.0/0.5.
"""

from dalston.common.pipeline_types import TranscriptMetadata
from dalston.common.transcript import (
    assemble_per_channel_transcript,
    assemble_transcript,
)

_PREPARE = {
    "channel_files": [
        {
            "artifact_id": "a1",
            "format": "wav",
            "duration": 10.0,
            "sample_rate": 16000,
            "channels": 1,
        }
    ],
    "engine_id": "audio-prepare",
}


def _transcribe_output(confidence: float | None = None) -> dict:
    out = {
        "text": "Hello",
        "language": "hr",
        "segments": [{"start": 0.0, "end": 2.0, "text": "Hello"}],
        "engine_id": "nemo",
    }
    if confidence is not None:
        out["language_confidence"] = confidence
    return out


class TestMetadataSchema:
    def test_language_confidence_defaults_to_none(self):
        md = TranscriptMetadata(
            audio_duration=1.0,
            audio_channels=1,
            sample_rate=16000,
            language="hr",
            word_timestamps=False,
            word_timestamps_requested=False,
            speaker_detection="none",
            speaker_count=0,
            created_at="2026-07-23T00:00:00Z",
            completed_at="2026-07-23T00:00:00Z",
        )
        assert md.language_confidence is None


class TestAssemblyNoFabrication:
    def test_standard_assembly_null_when_engine_omits(self):
        stage_outputs = {"prepare": _PREPARE, "transcribe": _transcribe_output()}
        result = assemble_transcript(job_id="j1", stage_outputs=stage_outputs)
        assert result.metadata.language_confidence is None

    def test_standard_assembly_preserves_real_confidence(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe": _transcribe_output(confidence=0.8734),
        }
        result = assemble_transcript(job_id="j2", stage_outputs=stage_outputs)
        assert result.metadata.language_confidence == 0.873

    def test_per_channel_null_when_engine_omits(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe_ch0": _transcribe_output(),
            "transcribe_ch1": _transcribe_output(),
        }
        result = assemble_per_channel_transcript(
            job_id="j3", stage_outputs=stage_outputs, channel_count=2
        )
        assert result.metadata.language_confidence is None

    def test_per_channel_preserves_real_confidence(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe_ch0": _transcribe_output(confidence=0.6),
            "transcribe_ch1": _transcribe_output(),
        }
        result = assemble_per_channel_transcript(
            job_id="j4", stage_outputs=stage_outputs, channel_count=2
        )
        assert result.metadata.language_confidence == 0.6


class TestElevenLabsCompatConcession:
    def test_missing_confidence_maps_to_zero(self):
        from dalston.gateway.api.v1.speech_to_text import _resolve_language_fields

        code, prob = _resolve_language_fields(
            {"metadata": {"language": "hr", "language_confidence": None}}
        )
        assert code == "hr"
        assert prob == 0.0
