"""Unit tests for PII detection feature (M26).

Tests for:
- PII detection DAG builder integration
- PII types and enums
- Request/response model PII parameters
"""

from uuid import uuid4

import pytest

from dalston.common.models import (
    PIIDetectionTier,
    PIIEntityCategory,
    PIIRedactionMode,
)
from dalston.common.pipeline_types import (
    AudioRedactOutput,
    PIIDetectOutput,
    PIIEntity,
    PIIMetadata,
)
from dalston.gateway.models.requests import TranscriptionCreateParams
from dalston.gateway.models.responses import PIIEntityResponse, PIIInfo
from dalston.orchestrator.dag import (
    VALID_PII_DETECTION_TIERS,
    VALID_PII_REDACTION_MODES,
    build_task_dag,
)


class TestPIIEnums:
    """Tests for PII-related enums."""

    def test_pii_detection_tier_values(self):
        """Test PIIDetectionTier enum values."""
        assert PIIDetectionTier.FAST == "fast"
        assert PIIDetectionTier.STANDARD == "standard"
        assert PIIDetectionTier.THOROUGH == "thorough"

    def test_pii_redaction_mode_values(self):
        """Test PIIRedactionMode enum values."""
        assert PIIRedactionMode.SILENCE == "silence"
        assert PIIRedactionMode.BEEP == "beep"

    def test_pii_entity_category_values(self):
        """Test PIIEntityCategory enum values."""
        assert PIIEntityCategory.PII == "pii"
        assert PIIEntityCategory.PCI == "pci"
        assert PIIEntityCategory.PHI == "phi"


class TestPIIDAGBuilder:
    """Tests for PII detection in DAG builder."""

    @pytest.fixture
    def job_id(self):
        return uuid4()

    @pytest.fixture
    def audio_uri(self):
        return "s3://test-bucket/audio/test.wav"

    def test_pii_detection_disabled_by_default(self, job_id, audio_uri):
        """Test that PII detection is disabled by default."""
        tasks = build_task_dag(job_id, audio_uri, {})

        stages = [t.stage for t in tasks]
        assert "pii_detect" not in stages
        assert "audio_redact" not in stages

    def test_pii_detection_enabled(self, job_id, audio_uri):
        """Test that PII detection task is added when enabled."""
        parameters = {
            "pii_detection": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        assert "pii_detect" in stages
        assert "audio_redact" not in stages  # Not enabled by default

    def test_pii_detection_with_audio_redaction(self, job_id, audio_uri):
        """Test that audio redaction task is added when enabled."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        assert "pii_detect" in stages
        assert "audio_redact" in stages

    def test_pii_detection_depends_on_align(self, job_id, audio_uri):
        """Test that PII detection depends on alignment when available."""
        parameters = {
            "pii_detection": True,
            "timestamps_granularity": "word",  # Enables align stage
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        pii_task = task_by_stage["pii_detect"]

        # PII should depend on align (and transcribe)
        assert task_by_stage["transcribe"].id in pii_task.dependencies
        assert task_by_stage["align"].id in pii_task.dependencies

    def test_pii_detection_depends_on_diarize(self, job_id, audio_uri):
        """Test that PII detection depends on diarization when enabled."""
        parameters = {
            "pii_detection": True,
            "speaker_detection": "diarize",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        pii_task = task_by_stage["pii_detect"]

        # PII should depend on diarize
        assert task_by_stage["diarize"].id in pii_task.dependencies

    def test_audio_redact_depends_on_pii_detect(self, job_id, audio_uri):
        """Test that audio redaction depends on PII detection."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        redact_task = task_by_stage["audio_redact"]

        # Audio redact should depend on pii_detect
        assert task_by_stage["pii_detect"].id in redact_task.dependencies

    def test_merge_depends_on_pii_tasks(self, job_id, audio_uri):
        """Test that merge depends on PII detection and audio redaction."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        merge_task = task_by_stage["merge"]

        # Merge should depend on both PII tasks
        assert task_by_stage["pii_detect"].id in merge_task.dependencies
        assert task_by_stage["audio_redact"].id in merge_task.dependencies

    def test_pii_detection_tier_config(self, job_id, audio_uri):
        """Test that PII detection tier is passed to config."""
        parameters = {
            "pii_detection": True,
            "pii_detection_tier": "thorough",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        pii_task = next(t for t in tasks if t.stage == "pii_detect")
        assert pii_task.config["detection_tier"] == "thorough"

    def test_pii_detection_tier_default(self, job_id, audio_uri):
        """Test that PII detection tier defaults to standard."""
        parameters = {
            "pii_detection": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        pii_task = next(t for t in tasks if t.stage == "pii_detect")
        assert pii_task.config["detection_tier"] == "standard"

    def test_pii_entity_types_config(self, job_id, audio_uri):
        """Test that PII entity types are passed to config."""
        parameters = {
            "pii_detection": True,
            "pii_entity_types": ["credit_card_number", "phone_number"],
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        pii_task = next(t for t in tasks if t.stage == "pii_detect")
        assert pii_task.config["entity_types"] == ["credit_card_number", "phone_number"]

    def test_audio_redaction_mode_config(self, job_id, audio_uri):
        """Test that audio redaction mode is passed to config."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
            "pii_redaction_mode": "beep",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        redact_task = next(t for t in tasks if t.stage == "audio_redact")
        assert redact_task.config["redaction_mode"] == "beep"

    def test_audio_redaction_mode_default(self, job_id, audio_uri):
        """Test that audio redaction mode defaults to silence."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        redact_task = next(t for t in tasks if t.stage == "audio_redact")
        assert redact_task.config["redaction_mode"] == "silence"

    def test_merge_config_includes_pii_flag(self, job_id, audio_uri):
        """Test that merge config includes pii_detection flag."""
        parameters = {
            "pii_detection": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        merge_task = next(t for t in tasks if t.stage == "merge")
        assert merge_task.config.get("pii_detection") is True

    def test_valid_pii_detection_tiers(self):
        """Test that valid PII detection tiers are defined."""
        assert "fast" in VALID_PII_DETECTION_TIERS
        assert "standard" in VALID_PII_DETECTION_TIERS
        assert "thorough" in VALID_PII_DETECTION_TIERS

    def test_valid_pii_redaction_modes(self):
        """Test that valid PII redaction modes are defined."""
        assert "silence" in VALID_PII_REDACTION_MODES
        assert "beep" in VALID_PII_REDACTION_MODES


class TestPIIPipelineTypes:
    """Tests for PII-related pipeline types."""

    def test_pii_entity_creation(self):
        """Test creating a PIIEntity."""
        entity = PIIEntity(
            entity_type="credit_card_number",
            category=PIIEntityCategory.PCI,
            start_offset=10,
            end_offset=26,
            start_time=1.5,
            end_time=3.2,
            confidence=0.95,
            speaker="SPEAKER_00",
            redacted_value="****7890",
            original_text="4111111111117890",
        )

        assert entity.entity_type == "credit_card_number"
        assert entity.category == PIIEntityCategory.PCI
        assert entity.confidence == 0.95

    def test_pii_detect_output_creation(self):
        """Test creating a PIIDetectOutput."""
        entity = PIIEntity(
            entity_type="phone_number",
            category=PIIEntityCategory.PII,
            start_offset=0,
            end_offset=14,
            start_time=0.0,
            end_time=1.5,
            confidence=0.9,
            speaker=None,
            redacted_value="****5678",
            original_text="+1-555-123-5678",
        )

        output = PIIDetectOutput(
            entities=[entity],
            redacted_text="Call me at [PHONE_NUMBER]",
            entity_count_by_type={"phone_number": 1},
            entity_count_by_category={"pii": 1},
            detection_tier=PIIDetectionTier.STANDARD,
            processing_time_ms=150,
            engine_id="pii-presidio",
        )

        assert len(output.entities) == 1
        assert output.redacted_text == "Call me at [PHONE_NUMBER]"
        assert output.detection_tier == PIIDetectionTier.STANDARD

    def test_pii_metadata_creation(self):
        """Test creating PIIMetadata."""
        metadata = PIIMetadata(
            detection_tier=PIIDetectionTier.FAST,
            entities_detected=5,
            entity_count_by_type={"credit_card_number": 2, "phone_number": 3},
            entity_count_by_category={"pci": 2, "pii": 3},
            redacted_audio_uri="s3://bucket/jobs/123/audio/redacted.wav",
            processing_time_ms=42,
        )

        assert metadata.entities_detected == 5
        assert metadata.entity_count_by_type["credit_card_number"] == 2

    def test_audio_redact_output_creation(self):
        """Test creating AudioRedactOutput."""
        output = AudioRedactOutput(
            redacted_audio_uri="s3://bucket/jobs/123/audio/redacted.wav",
            redaction_mode=PIIRedactionMode.SILENCE,
            buffer_ms=50,
            entities_redacted=3,
            redaction_map=[
                {
                    "start_time": 1.5,
                    "end_time": 3.2,
                    "entity_types": ["credit_card_number"],
                },
            ],
            engine_id="audio-redactor",
        )

        assert output.redaction_mode == PIIRedactionMode.SILENCE
        assert output.entities_redacted == 3


class TestPIIRequestParameters:
    """Tests for PII parameters in request models."""

    def test_pii_detection_defaults(self):
        """Test that PII detection is disabled by default."""
        params = TranscriptionCreateParams()

        assert params.pii_detection is False
        assert params.pii_detection_tier == "standard"
        assert params.pii_entity_types is None
        assert params.redact_pii is False
        assert params.redact_pii_audio is False
        assert params.pii_redaction_mode == "silence"

    def test_pii_detection_enabled_in_job_params(self):
        """Test that PII parameters are included in job parameters."""
        params = TranscriptionCreateParams(
            pii_detection=True,
            pii_detection_tier="thorough",
            pii_entity_types=["credit_card_number", "ssn"],
            redact_pii=True,
            redact_pii_audio=True,
            pii_redaction_mode="beep",
        )

        job_params = params.to_job_parameters()

        assert job_params["pii_detection"] is True
        assert job_params["pii_detection_tier"] == "thorough"
        assert job_params["pii_entity_types"] == ["credit_card_number", "ssn"]
        assert job_params["redact_pii"] is True
        assert job_params["redact_pii_audio"] is True
        assert job_params["pii_redaction_mode"] == "beep"

    def test_pii_detection_disabled_not_in_params(self):
        """Test that PII parameters are not included when disabled."""
        params = TranscriptionCreateParams(pii_detection=False)

        job_params = params.to_job_parameters()

        assert "pii_detection" not in job_params


class TestPIIResponseModels:
    """Tests for PII-related response models."""

    def test_pii_entity_response(self):
        """Test PIIEntityResponse model."""
        response = PIIEntityResponse(
            entity_type="email_address",
            category="pii",
            start_offset=10,
            end_offset=30,
            start_time=1.0,
            end_time=2.0,
            confidence=0.95,
            speaker=None,
            redacted_value="****@example.com",
        )

        assert response.entity_type == "email_address"
        assert response.category == "pii"

    def test_pii_info_response(self):
        """Test PIIInfo model."""
        info = PIIInfo(
            enabled=True,
            detection_tier="standard",
            entities_detected=5,
            entity_summary={"credit_card_number": 2, "phone_number": 3},
            redacted_audio_available=True,
        )

        assert info.enabled is True
        assert info.entities_detected == 5
        assert info.entity_summary["credit_card_number"] == 2
