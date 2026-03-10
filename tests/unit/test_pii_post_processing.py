"""Unit tests for M67 PII post-processing.

Tests cover:
- DAG builder omits PII stages (always post-processed)
- PostProcessor task creation and dependency wiring
- Feature flag routing
"""

from uuid import UUID, uuid4

import pytest

from dalston.orchestrator.dag import DEFAULT_ENGINES
from dalston.orchestrator.post_processor import (
    EnrichmentStatus,
    build_post_processing_tasks,
    is_post_processing_task,
    needs_post_processing,
)
from tests.dag_test_helpers import build_task_dag_for_test

# =============================================================================
# DAG Builder Tests
# =============================================================================


class TestDagBuilderPiiStages:
    """PII stages are always omitted from the core pipeline DAG."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_pii_stages_omitted_from_dag(self, job_id: UUID, audio_uri: str):
        """PII detection and audio redaction are not included in the DAG."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        assert "pii_detect" not in stages
        assert "audio_redact" not in stages

    def test_core_pipeline_stages_present(self, job_id: UUID, audio_uri: str):
        """Core pipeline stages are still built when PII is enabled."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        assert "prepare" in stages
        assert "transcribe" in stages
        assert "merge" in stages

    def test_merge_has_no_pii_config(self, job_id: UUID, audio_uri: str):
        """Merge task should not include PII config."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        merge_task = next(t for t in tasks if t.stage == "merge")
        assert "pii_detection" not in merge_task.config
        assert merge_task.input_bindings == []

    def test_merge_depends_only_on_core_stages(self, job_id: UUID, audio_uri: str):
        """Merge should not depend on PII tasks."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        merge_deps = task_by_stage["merge"].dependencies

        assert task_by_stage["prepare"].id in merge_deps
        assert task_by_stage["transcribe"].id in merge_deps
        assert task_by_stage["align"].id in merge_deps

    def test_pii_disabled_dag_unchanged(self, job_id: UUID, audio_uri: str):
        """When PII is not enabled, the DAG is unaffected."""
        parameters_no_pii = {"timestamps_granularity": "word"}
        parameters_pii = {"timestamps_granularity": "word", "pii_detection": True}

        tasks_no_pii = build_task_dag_for_test(job_id, audio_uri, parameters_no_pii)
        tasks_pii = build_task_dag_for_test(job_id, audio_uri, parameters_pii)

        assert len(tasks_no_pii) == len(tasks_pii)
        assert {t.stage for t in tasks_no_pii} == {t.stage for t in tasks_pii}


class TestDagBuilderPerChannelPiiStages:
    """Per-channel DAG omits PII stages."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_per_channel_pii_stages_omitted(self, job_id: UUID, audio_uri: str):
        """Per-channel PII stages are not included in the DAG."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        assert "pii_detect_ch0" not in stages
        assert "pii_detect_ch1" not in stages
        assert "audio_redact_ch0" not in stages
        assert "audio_redact_ch1" not in stages
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "merge" in stages

    def test_per_channel_merge_no_pii_flags(self, job_id: UUID, audio_uri: str):
        """Per-channel merge should not have PII flags."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        merge_task = next(t for t in tasks if t.stage == "merge")
        assert "pii_detection" not in merge_task.config
        assert "redact_pii_audio" not in merge_task.config
        assert merge_task.input_bindings == []

    def test_per_channel_task_count(self, job_id: UUID, audio_uri: str):
        """Per-channel DAG has correct task count without PII stages."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "timestamps_granularity": "word",
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        # prepare + 2*transcribe + 2*align + merge = 6
        assert len(tasks) == 6


# =============================================================================
# PostProcessor Tests
# =============================================================================


class _FakeJobModel:
    """Minimal fake job model for unit testing."""

    def __init__(self, job_id: UUID, parameters: dict, status: str = "completed"):
        self.id = job_id
        self.parameters = parameters
        self.status = status


class TestNeedsPostProcessing:
    """Tests for needs_post_processing helper."""

    def test_with_pii_returns_true(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True})
        assert needs_post_processing(job) is True

    def test_without_pii_returns_false(self):
        job = _FakeJobModel(uuid4(), {})
        assert needs_post_processing(job) is False

    def test_pii_false_returns_false(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": False})
        assert needs_post_processing(job) is False

    def test_none_parameters_returns_false(self):
        job = _FakeJobModel(uuid4(), None)  # type: ignore[arg-type]
        assert needs_post_processing(job) is False


class TestBuildPostProcessingTasks:
    """Tests for build_post_processing_tasks."""

    def test_creates_pii_detect_task(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True})

        tasks = build_post_processing_tasks(job)

        assert len(tasks) == 1
        assert tasks[0].stage == "pii_detect"
        assert tasks[0].required is False
        assert tasks[0].config.get("post_processing") is True

    def test_creates_pii_detect_and_audio_redact(self):
        job = _FakeJobModel(
            uuid4(),
            {
                "pii_detection": True,
                "redact_pii_audio": True,
                "pii_redaction_mode": "beep",
                "pii_buffer_ms": 100,
            },
        )

        tasks = build_post_processing_tasks(job)

        assert len(tasks) == 2
        assert tasks[0].stage == "pii_detect"
        assert tasks[1].stage == "audio_redact"
        assert tasks[1].config["redaction_mode"] == "beep"
        assert tasks[1].config["buffer_ms"] == 100
        assert tasks[1].config.get("post_processing") is True

    def test_audio_redact_depends_on_pii_detect(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True, "redact_pii_audio": True})

        tasks = build_post_processing_tasks(job)

        pii_detect = tasks[0]
        audio_redact = tasks[1]
        assert pii_detect.id in audio_redact.dependencies

    def test_pii_detect_has_no_dependencies(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True})

        tasks = build_post_processing_tasks(job)

        assert tasks[0].dependencies == []

    def test_respects_confidence_threshold(self):
        job = _FakeJobModel(
            uuid4(),
            {"pii_detection": True, "pii_confidence_threshold": 0.8},
        )

        tasks = build_post_processing_tasks(job)

        assert tasks[0].config["confidence_threshold"] == 0.8

    def test_respects_entity_types(self):
        job = _FakeJobModel(
            uuid4(),
            {"pii_detection": True, "pii_entity_types": ["email", "phone"]},
        )

        tasks = build_post_processing_tasks(job)

        assert tasks[0].config["entity_types"] == ["email", "phone"]

    def test_invalid_redaction_mode_defaults_to_silence(self):
        job = _FakeJobModel(
            uuid4(),
            {
                "pii_detection": True,
                "redact_pii_audio": True,
                "pii_redaction_mode": "invalid",
            },
        )

        tasks = build_post_processing_tasks(job)

        assert tasks[1].config["redaction_mode"] == "silence"

    def test_runtime_model_id_propagated(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True})

        tasks = build_post_processing_tasks(
            job, stage_runtime_model_ids={"pii_detect": "urchade/gliner_multi-v2.1"}
        )

        assert tasks[0].config["runtime_model_id"] == "urchade/gliner_multi-v2.1"

    def test_uses_default_engines(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True, "redact_pii_audio": True})

        tasks = build_post_processing_tasks(job)

        assert tasks[0].runtime == DEFAULT_ENGINES["pii_detect"]
        assert tasks[1].runtime == DEFAULT_ENGINES["audio_redact"]


class TestIsPostProcessingTask:
    """Tests for is_post_processing_task helper."""

    def test_returns_true_for_post_processing_task(self):
        class FakeTask:
            config = {"post_processing": True, "entity_types": None}

        assert is_post_processing_task(FakeTask()) is True

    def test_returns_false_for_pipeline_task(self):
        class FakeTask:
            config = {"entity_types": None}

        assert is_post_processing_task(FakeTask()) is False

    def test_returns_false_for_empty_config(self):
        class FakeTask:
            config = {}

        assert is_post_processing_task(FakeTask()) is False


# =============================================================================
# EnrichmentStatus Tests
# =============================================================================


class TestEnrichmentStatus:
    """Tests for enrichment status enum values."""

    def test_status_values(self):
        assert EnrichmentStatus.PENDING.value == "pending"
        assert EnrichmentStatus.RUNNING.value == "running"
        assert EnrichmentStatus.COMPLETED.value == "completed"
        assert EnrichmentStatus.FAILED.value == "failed"
