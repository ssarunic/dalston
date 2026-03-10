"""Unit tests for M67 PII post-processing.

Tests cover:
- DAG builder never includes PII stages (PII is post-processing only)
- PostProcessor task creation and dependency wiring
- Parity comparison helpers
- Enrichment status tracking
"""

from uuid import UUID, uuid4

import pytest

from dalston.orchestrator.dag import DEFAULT_ENGINES
from dalston.orchestrator.parity import (
    ParityResult,
    compare_audio_redaction,
    compare_pii_entities,
    compare_pii_outputs,
    compare_redacted_text,
)
from dalston.orchestrator.post_processor import (
    EnrichmentStatus,
    build_post_processing_tasks,
    is_post_processing_task,
    needs_post_processing,
)
from tests.dag_test_helpers import build_task_dag_for_test

# =============================================================================
# DAG Builder: PII stages never in DAG
# =============================================================================


class TestDagBuilderNoPiiStages:
    """Verify PII stages are never created in the DAG."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_pii_enabled_no_pii_stages_in_dag(self, job_id: UUID, audio_uri: str):
        """Even with pii_detection=True, no PII stages appear in the DAG."""
        parameters = {
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        assert "pii_detect" not in stages
        assert "audio_redact" not in stages
        # Core pipeline stages still present
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
        """Merge should only depend on core pipeline tasks."""
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

    def test_core_pipeline_task_count_unchanged_by_pii(
        self, job_id: UUID, audio_uri: str
    ):
        """PII flags should not affect task count."""
        params_no_pii = {"timestamps_granularity": "word"}
        params_with_pii = {
            "timestamps_granularity": "word",
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks_no_pii = build_task_dag_for_test(job_id, audio_uri, params_no_pii)
        tasks_with_pii = build_task_dag_for_test(job_id, audio_uri, params_with_pii)

        assert len(tasks_no_pii) == len(tasks_with_pii)


class TestDagBuilderPerChannelNoPiiStages:
    """Verify per-channel DAGs never include PII stages."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_per_channel_no_pii_stages(self, job_id: UUID, audio_uri: str):
        """Per-channel pipelines should not include PII stages."""
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
        # Core per-channel stages still present
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

    def test_per_channel_task_count_unaffected_by_pii(
        self, job_id: UUID, audio_uri: str
    ):
        """Per-channel with PII should have same task count as without PII."""
        params_no_pii = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "timestamps_granularity": "word",
        }
        params_with_pii = {
            **params_no_pii,
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks_no_pii = build_task_dag_for_test(job_id, audio_uri, params_no_pii)
        tasks_with_pii = build_task_dag_for_test(job_id, audio_uri, params_with_pii)

        # prepare + 2*transcribe + 2*align + merge = 6
        assert len(tasks_no_pii) == 6
        assert len(tasks_with_pii) == 6


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

    def test_pii_enabled_returns_true(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": True})
        assert needs_post_processing(job) is True

    def test_pii_disabled_returns_false(self):
        job = _FakeJobModel(uuid4(), {})
        assert needs_post_processing(job) is False

    def test_pii_false_returns_false(self):
        job = _FakeJobModel(uuid4(), {"pii_detection": False})
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
# Parity Comparison Tests
# =============================================================================


class TestCompareRedactedText:
    """Tests for redacted text comparison."""

    def test_identical_text_matches(self):
        match, diffs = compare_redacted_text(
            "Hello [REDACTED] world", "Hello [REDACTED] world"
        )
        assert match is True
        assert diffs == []

    def test_whitespace_only_difference_matches(self):
        match, diffs = compare_redacted_text(
            "Hello  [REDACTED]  world", "Hello [REDACTED] world"
        )
        assert match is True
        assert "whitespace_only_difference" in diffs

    def test_different_text_does_not_match(self):
        match, diffs = compare_redacted_text(
            "Hello [EMAIL] world", "Hello [PHONE] world"
        )
        assert match is False
        assert len(diffs) > 0

    def test_different_length_reported(self):
        match, diffs = compare_redacted_text("Hello world", "Hello")
        assert match is False
        assert any("length_mismatch" in d for d in diffs)


class TestComparePiiEntities:
    """Tests for PII entity comparison."""

    def test_identical_entities_match(self):
        entities = [
            {
                "entity_type": "email",
                "start_offset": 10,
                "end_offset": 25,
                "redacted_value": "[EMAIL]",
                "category": "pii",
            }
        ]
        match, diffs = compare_pii_entities(entities, entities)
        assert match is True
        assert diffs == []

    def test_different_entity_types_mismatch(self):
        pipeline = [{"entity_type": "email", "start_offset": 10, "end_offset": 25}]
        post_proc = [{"entity_type": "phone", "start_offset": 10, "end_offset": 25}]
        match, diffs = compare_pii_entities(pipeline, post_proc)
        assert match is False

    def test_different_count_reported(self):
        pipeline = [{"entity_type": "email", "start_offset": 10, "end_offset": 25}]
        post_proc = []
        match, diffs = compare_pii_entities(pipeline, post_proc)
        assert match is False
        assert any("entity_count_mismatch" in d for d in diffs)

    def test_timing_fields_ignored(self):
        """Timing fields like start_time/end_time should not affect comparison."""
        pipeline = [
            {
                "entity_type": "email",
                "start_offset": 10,
                "end_offset": 25,
                "start_time": 1.0,
                "end_time": 2.0,
            }
        ]
        post_proc = [
            {
                "entity_type": "email",
                "start_offset": 10,
                "end_offset": 25,
                "start_time": 1.05,
                "end_time": 2.05,
            }
        ]
        match, diffs = compare_pii_entities(pipeline, post_proc)
        assert match is True

    def test_entities_sorted_before_comparison(self):
        """Order of entities should not matter."""
        e1 = {"entity_type": "email", "start_offset": 10, "end_offset": 25}
        e2 = {"entity_type": "phone", "start_offset": 30, "end_offset": 45}

        match, diffs = compare_pii_entities([e2, e1], [e1, e2])
        assert match is True


class TestCompareAudioRedaction:
    """Tests for audio redaction map comparison."""

    def test_identical_maps_match(self):
        redaction_map = [[1.0, 2.0], [5.0, 6.0]]
        match, diffs = compare_audio_redaction(redaction_map, redaction_map)
        assert match is True

    def test_within_tolerance_matches(self):
        pipeline = [[1.0, 2.0]]
        post_proc = [[1.04, 2.04]]  # 40ms difference, within default 50ms
        match, diffs = compare_audio_redaction(pipeline, post_proc)
        assert match is True

    def test_beyond_tolerance_mismatches(self):
        pipeline = [[1.0, 2.0]]
        post_proc = [[1.1, 2.1]]  # 100ms difference, beyond 50ms tolerance
        match, diffs = compare_audio_redaction(pipeline, post_proc)
        assert match is False
        assert any("timing_mismatch" in d for d in diffs)

    def test_different_count_mismatches(self):
        pipeline = [[1.0, 2.0], [5.0, 6.0]]
        post_proc = [[1.0, 2.0]]
        match, diffs = compare_audio_redaction(pipeline, post_proc)
        assert match is False
        assert any("redaction_count_mismatch" in d for d in diffs)


class TestComparePiiOutputs:
    """Tests for full parity comparison."""

    def test_identical_outputs_equivalent(self):
        output = {
            "redacted_text": "Hello [EMAIL] world",
            "pii_entities": [
                {"entity_type": "email", "start_offset": 6, "end_offset": 13}
            ],
        }

        result = compare_pii_outputs(output, output)

        assert result.is_equivalent is True
        assert result.text_match is True
        assert result.entity_match is True
        assert result.audio_match is None

    def test_text_mismatch_not_equivalent(self):
        pipeline = {
            "redacted_text": "Hello [EMAIL]",
            "pii_entities": [],
        }
        post_proc = {
            "redacted_text": "Hello [PHONE]",
            "pii_entities": [],
        }

        result = compare_pii_outputs(pipeline, post_proc)

        assert result.is_equivalent is False
        assert result.text_match is False

    def test_with_audio_redaction_equivalent(self):
        output = {
            "redacted_text": "Hello [EMAIL]",
            "pii_entities": [
                {"entity_type": "email", "start_offset": 6, "end_offset": 13}
            ],
            "redaction_map": [[1.0, 2.0]],
        }

        result = compare_pii_outputs(output, output)

        assert result.is_equivalent is True
        assert result.audio_match is True

    def test_audio_presence_mismatch(self):
        pipeline = {
            "redacted_text": "Hello",
            "pii_entities": [],
            "redaction_map": [[1.0, 2.0]],
        }
        post_proc = {
            "redacted_text": "Hello",
            "pii_entities": [],
        }

        result = compare_pii_outputs(pipeline, post_proc)

        assert result.is_equivalent is False
        assert result.audio_match is False

    def test_parity_result_summary(self):
        result = ParityResult(
            is_equivalent=False,
            text_match=False,
            entity_match=True,
            audio_match=None,
            differences=["text_diff_1"],
        )
        summary = result.summary()
        assert "FAIL" in summary
        assert "text_mismatch" in summary


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
