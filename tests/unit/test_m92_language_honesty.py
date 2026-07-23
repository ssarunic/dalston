"""Unit tests for M92 step 92.3: honest language handling.

Engines that cannot force a decode language (NeMo, ONNX parakeet) now mark
the language field as an echo via language_source="requested" and warn;
engines that detect language (faster-whisper) mark it "detected".
"""

import pytest
from pydantic import ValidationError

from dalston.common.pipeline_types import Transcript, TranscriptMetadata
from dalston.common.transcript import (
    assemble_per_channel_transcript,
    assemble_transcript,
)
from dalston.engine_sdk.types import EngineCapabilities

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


def _transcribe_output(language_source: str | None = None) -> dict:
    out = {
        "text": "Hello world",
        "language": "hr",
        "segments": [{"start": 0.0, "end": 2.0, "text": "Hello world"}],
        "engine_id": "nemo",
    }
    if language_source is not None:
        out["language_source"] = language_source
    return out


class TestLanguageSourceSchema:
    def test_transcript_accepts_language_source(self):
        t = Transcript.model_validate(_transcribe_output("requested"))
        assert t.language_source == "requested"

    def test_transcript_language_source_defaults_none(self):
        t = Transcript.model_validate(_transcribe_output())
        assert t.language_source is None

    def test_transcript_rejects_unknown_language_source(self):
        with pytest.raises(ValidationError):
            Transcript.model_validate(_transcribe_output("guessed"))

    def test_metadata_accepts_language_source(self):
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
            language_source="requested",
        )
        assert md.language_source == "requested"


class TestLanguageSourcePropagation:
    def test_standard_assembly_propagates(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe": _transcribe_output("requested"),
        }
        result = assemble_transcript(job_id="j1", stage_outputs=stage_outputs)
        assert result.metadata.language_source == "requested"

    def test_standard_assembly_none_when_absent(self):
        stage_outputs = {"prepare": _PREPARE, "transcribe": _transcribe_output()}
        result = assemble_transcript(job_id="j2", stage_outputs=stage_outputs)
        assert result.metadata.language_source is None

    def test_per_channel_uses_channel_zero(self):
        stage_outputs = {
            "prepare": _PREPARE,
            "transcribe_ch0": _transcribe_output("requested"),
            "transcribe_ch1": _transcribe_output("detected"),
        }
        result = assemble_per_channel_transcript(
            job_id="j3", stage_outputs=stage_outputs, channel_count=2
        )
        assert result.metadata.language_source == "requested"


class TestEngineCapabilitiesLanguageForcing:
    def test_default_is_true(self):
        caps = EngineCapabilities(engine_id="x", version="1", stages=["transcribe"])
        assert caps.supports_language_forcing is True

    def test_can_declare_false(self):
        caps = EngineCapabilities(
            engine_id="nemo",
            version="1",
            stages=["transcribe"],
            supports_language_forcing=False,
        )
        assert caps.supports_language_forcing is False
