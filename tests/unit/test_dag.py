"""Unit tests for DAG builder with model selection.

These tests require the orchestrator extras (asyncpg, sqlalchemy).
Run with: uv run --extra dev --extra orchestrator pytest tests/unit/test_dag.py
"""

from uuid import UUID, uuid4

import pytest

from dalston.orchestrator.dag import (
    DEFAULT_ENGINES,
    DEFAULT_TRANSCRIBE_CONFIG,
    build_task_dag,
)


class TestBuildTaskDagModelSelection:
    """Tests for model selection in DAG builder."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_default_model_config(self, job_id: UUID, audio_uri: str):
        """Test that default model config is used when no model specified."""
        tasks = build_task_dag(job_id, audio_uri, {})

        # Find transcribe task
        transcribe_task = next(t for t in tasks if t.stage == "transcribe")

        assert transcribe_task.engine_id == DEFAULT_ENGINES["transcribe"]
        assert transcribe_task.config["model"] == DEFAULT_TRANSCRIBE_CONFIG["model"]

    def test_transcribe_config_from_parameters(self, job_id: UUID, audio_uri: str):
        """Test that transcribe_config from parameters is used."""
        parameters = {
            "transcribe_config": {
                "model": "base",  # Engine model name
            },
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.config["model"] == "base"

    def test_engine_override_from_parameters(self, job_id: UUID, audio_uri: str):
        """Test that engine_transcribe override is respected."""
        parameters = {
            "engine_transcribe": "custom-engine",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.engine_id == "custom-engine"

    def test_model_registry_integration(self, job_id: UUID, audio_uri: str):
        """Test parameters as they would be passed from gateway with model registry."""
        # Simulates what the gateway passes after resolving a model
        parameters = {
            "model": "whisper-base",  # User-facing model ID
            "engine_transcribe": "faster-whisper",  # From ModelDefinition.engine
            "transcribe_config": {
                "model": "base",  # From ModelDefinition.engine_model
            },
            "language": "en",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.engine_id == "faster-whisper"
        assert transcribe_task.config["model"] == "base"
        assert transcribe_task.config["language"] == "en"

    def test_language_override_in_transcribe_config(self, job_id: UUID, audio_uri: str):
        """Test that top-level language overrides transcribe_config language."""
        parameters = {
            "transcribe_config": {
                "model": "large-v3",
                "language": "auto",  # From transcribe_config
            },
            "language": "fr",  # Top-level override
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        # Top-level language should win
        assert transcribe_task.config["language"] == "fr"

    def test_transcribe_config_merges_with_defaults(self, job_id: UUID, audio_uri: str):
        """Test that transcribe_config merges with defaults."""
        parameters = {
            "transcribe_config": {
                "model": "small",
                # beam_size and vad_filter not specified - should use defaults
            },
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.config["model"] == "small"
        assert transcribe_task.config["beam_size"] == DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
        assert transcribe_task.config["vad_filter"] == DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]

    def test_legacy_model_parameter(self, job_id: UUID, audio_uri: str):
        """Test legacy behavior with top-level model parameter."""
        # Legacy: direct model parameter (without transcribe_config)
        parameters = {
            "model": "medium",
            "language": "de",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        # Legacy behavior: top-level model goes to config
        assert transcribe_task.config["model"] == "medium"
        assert transcribe_task.config["language"] == "de"


class TestBuildTaskDagPipeline:
    """Tests for DAG pipeline structure."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_basic_pipeline_structure(self, job_id: UUID, audio_uri: str):
        """Test basic pipeline: prepare → transcribe → align → merge."""
        tasks = build_task_dag(job_id, audio_uri, {"timestamps_granularity": "word"})

        stages = [t.stage for t in tasks]
        assert "prepare" in stages
        assert "transcribe" in stages
        assert "align" in stages
        assert "merge" in stages

    def test_no_align_when_segment_granularity(self, job_id: UUID, audio_uri: str):
        """Test that align stage is skipped with segment granularity."""
        tasks = build_task_dag(job_id, audio_uri, {"timestamps_granularity": "segment"})

        stages = [t.stage for t in tasks]
        assert "align" not in stages

    def test_diarize_stage_added(self, job_id: UUID, audio_uri: str):
        """Test that diarize stage is added with speaker_detection=diarize."""
        tasks = build_task_dag(job_id, audio_uri, {"speaker_detection": "diarize"})

        stages = [t.stage for t in tasks]
        assert "diarize" in stages

    def test_task_dependencies_correct(self, job_id: UUID, audio_uri: str):
        """Test that task dependencies are wired correctly."""
        tasks = build_task_dag(job_id, audio_uri, {"timestamps_granularity": "word"})

        task_by_stage = {t.stage: t for t in tasks}

        # Prepare has no dependencies
        assert task_by_stage["prepare"].dependencies == []

        # Transcribe depends on prepare
        assert task_by_stage["prepare"].id in task_by_stage["transcribe"].dependencies

        # Align depends on transcribe
        assert task_by_stage["transcribe"].id in task_by_stage["align"].dependencies

        # Merge depends on multiple tasks
        merge_deps = task_by_stage["merge"].dependencies
        assert task_by_stage["prepare"].id in merge_deps
        assert task_by_stage["transcribe"].id in merge_deps
        assert task_by_stage["align"].id in merge_deps
