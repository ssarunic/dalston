"""Phase-0 contract tests for M51 stateless engine refactor."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dalston.common.artifacts import ArtifactSelector, ProducedArtifact, RequestBinding
from dalston.common.pipeline_types import AudioMedia, PIIMetadata, RedactionResponse


class TestUriFreeMediaContracts:
    """Audio payload contracts use artifact references, not URIs."""

    def test_audio_media_requires_artifact_id(self) -> None:
        media = AudioMedia(
            artifact_id="artf_audio_source",
            format="wav",
            duration=12.5,
            sample_rate=16000,
            channels=1,
            bit_depth=16,
        )

        assert media.artifact_id == "artf_audio_source"

    def test_audio_media_rejects_legacy_uri_field(self) -> None:
        with pytest.raises(ValidationError):
            AudioMedia(
                uri="s3://bucket/audio.wav",
                format="wav",
                duration=12.5,
                sample_rate=16000,
                channels=1,
            )

    def test_audio_redaction_models_use_artifact_id_fields(self) -> None:
        pii_metadata = PIIMetadata(
            entities_detected=2,
            entity_count_by_type={"credit_card": 2},
            entity_count_by_category={"pci": 2},
            redacted_audio_artifact_id="artf_audio_redacted",
            processing_time_ms=10,
        )
        redact_response = RedactionResponse(
            redacted_audio_artifact_id="artf_audio_redacted",
            redaction_mode="silence",
            buffer_ms=50,
            entities_redacted=2,
            redaction_map=[],
            engine_id="audio-redactor",
        )

        assert pii_metadata.redacted_audio_artifact_id == "artf_audio_redacted"
        assert redact_response.redacted_audio_artifact_id == "artf_audio_redacted"


class TestArtifactBindingContracts:
    """DAG binding contracts are explicit and selector-based."""

    def test_input_binding_selector_shape(self) -> None:
        binding = RequestBinding(
            slot="audio",
            selector=ArtifactSelector(
                producer_stage="prepare",
                kind="audio",
                channel=0,
                role="prepared",
                required=True,
            ),
        )

        assert binding.slot == "audio"
        assert binding.selector.producer_stage == "prepare"
        assert binding.selector.kind == "audio"
        assert binding.selector.channel == 0

    def test_produced_artifact_requires_local_path(self) -> None:
        produced = ProducedArtifact(
            logical_name="prepared_audio_ch0",
            local_path=Path("/tmp/prepared_ch0.wav"),
            kind="audio",
            channel=0,
            role="prepared",
            media_type="audio/wav",
        )

        assert produced.logical_name == "prepared_audio_ch0"
        assert produced.local_path.name == "prepared_ch0.wav"
