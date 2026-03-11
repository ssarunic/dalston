"""Unit tests for DAG builder with model selection.

These tests require the orchestrator extras (asyncpg, sqlalchemy).
Run with: uv run --extra dev --extra orchestrator pytest tests/unit/test_dag.py
"""

from uuid import UUID, uuid4

import pytest

from dalston.orchestrator.dag import DEFAULT_TRANSCRIBE_CONFIG
from tests.dag_test_helpers import build_task_dag_for_test


class TestBuildTaskDagModelSelection:
    """Tests for model selection in DAG builder."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_default_model_config(self, job_id: UUID, audio_uri: str):
        """Test that default config is used when no model specified.

        M36: With engine_id model management, the default engine (faster-whisper-large-v3-turbo)
        is resolved to its engine_id (faster-whisper).
        """
        tasks = build_task_dag_for_test(job_id, audio_uri, {})

        # Find transcribe task
        transcribe_task = next(t for t in tasks if t.stage == "transcribe")

        # M36: engine_id is the logical engine identifier
        # Default engine faster-whisper-large-v3-turbo maps to faster-whisper engine_id
        assert transcribe_task.engine_id == "faster-whisper"
        # Default config values are applied
        assert (
            transcribe_task.config["beam_size"]
            == DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
        )
        assert (
            transcribe_task.config["vad_filter"]
            == DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]
        )

    def test_transcribe_config_from_parameters(self, job_id: UUID, audio_uri: str):
        """Test that transcribe_config from parameters is used."""
        parameters = {
            "transcribe_config": {
                "model": "base",  # Engine model name
            },
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.config["model"] == "base"

    def test_engine_override_from_parameters(self, job_id: UUID, audio_uri: str):
        """Test that model_transcribe override is respected."""
        parameters = {
            "model_transcribe": "custom-engine",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.engine_id == "custom-engine"

    def test_model_registry_integration(self, job_id: UUID, audio_uri: str):
        """Test parameters as they would be passed from gateway with model registry."""
        # Simulates what the gateway passes after resolving a model
        parameters = {
            "model": "whisper-base",  # User-facing model ID
            "model_transcribe": "faster-whisper",  # From ModelDefinition.engine
            "transcribe_config": {
                "model": "base",  # From ModelDefinition.engine_model
            },
            "language": "en",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

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

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

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

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.config["model"] == "small"
        assert (
            transcribe_task.config["beam_size"]
            == DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
        )
        assert (
            transcribe_task.config["vad_filter"]
            == DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]
        )

    def test_prompt_is_preserved_in_transcribe_config(
        self, job_id: UUID, audio_uri: str
    ):
        tasks = build_task_dag_for_test(
            job_id,
            audio_uri,
            {"prompt": "ACME quarterly earnings call", "temperature": 0.0},
        )

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.config["prompt"] == "ACME quarterly earnings call"
        assert transcribe_task.config["temperature"] == 0.0

    def test_known_speaker_names_not_in_task_configs(
        self, job_id: UUID, audio_uri: str
    ):
        """For mono pipelines, known_speaker_names are passed to assemble_transcript
        in handlers, not stored in individual task configs."""
        tasks = build_task_dag_for_test(
            job_id,
            audio_uri,
            {
                "speaker_detection": "diarize",
                "known_speaker_names": ["Alice", "Bob"],
            },
        )

        # Mono pipeline has no merge task
        stages = [t.stage for t in tasks]
        assert "merge" not in stages

        # known_speaker_names should not appear in individual task configs
        for task in tasks:
            assert "known_speaker_names" not in task.config


class TestBuildTaskDagPipeline:
    """Tests for DAG pipeline structure."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_basic_pipeline_structure(self, job_id: UUID, audio_uri: str):
        """Test basic pipeline: prepare → transcribe → align (no merge for mono)."""
        tasks = build_task_dag_for_test(
            job_id, audio_uri, {"timestamps_granularity": "word"}
        )

        stages = [t.stage for t in tasks]
        assert "prepare" in stages
        assert "transcribe" in stages
        assert "align" in stages
        assert "merge" not in stages

    def test_no_align_when_segment_granularity(self, job_id: UUID, audio_uri: str):
        """Test that align stage is skipped with segment granularity."""
        tasks = build_task_dag_for_test(
            job_id, audio_uri, {"timestamps_granularity": "segment"}
        )

        stages = [t.stage for t in tasks]
        assert "align" not in stages

    def test_diarize_stage_added(self, job_id: UUID, audio_uri: str):
        """Test that diarize stage is added with speaker_detection=diarize."""
        tasks = build_task_dag_for_test(
            job_id, audio_uri, {"speaker_detection": "diarize"}
        )

        stages = [t.stage for t in tasks]
        assert "diarize" in stages

    def test_diarize_loaded_model_id_propagated(self, job_id: UUID, audio_uri: str):
        """Diarize config should include selected loaded_model_id."""
        tasks = build_task_dag_for_test(
            job_id,
            audio_uri,
            {
                "speaker_detection": "diarize",
                "model_diarize": "pyannote/speaker-diarization-community-1",
            },
        )

        diarize_task = next(t for t in tasks if t.stage == "diarize")
        assert (
            diarize_task.config["loaded_model_id"]
            == "pyannote/speaker-diarization-community-1"
        )

    def test_align_loaded_model_id_propagated(self, job_id: UUID, audio_uri: str):
        """Align config should include selected loaded_model_id."""
        tasks = build_task_dag_for_test(
            job_id,
            audio_uri,
            {
                "timestamps_granularity": "word",
                "model_align": "jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
            },
        )

        align_task = next(t for t in tasks if t.stage == "align")
        assert (
            align_task.config["loaded_model_id"]
            == "jonatasgrosman/wav2vec2-large-xlsr-53-japanese"
        )

    def test_task_dependencies_correct(self, job_id: UUID, audio_uri: str):
        """Test that task dependencies are wired correctly."""
        tasks = build_task_dag_for_test(
            job_id, audio_uri, {"timestamps_granularity": "word"}
        )

        task_by_stage = {t.stage: t for t in tasks}

        # Prepare has no dependencies
        assert task_by_stage["prepare"].dependencies == []

        # Transcribe depends on prepare
        assert task_by_stage["prepare"].id in task_by_stage["transcribe"].dependencies

        # Align depends on transcribe
        assert task_by_stage["transcribe"].id in task_by_stage["align"].dependencies

        # No merge in mono pipeline
        assert "merge" not in task_by_stage


class TestBuildTaskDagNemo:
    """Tests for NeMo engine_id DAG behavior (M21/M36).

    M36: Parakeet models now route through the 'nemo' engine_id.
    Using catalog entries like 'nvidia/parakeet-tdt-1.1b' (full HuggingFace namespace).
    """

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_nemo_skips_align_stage(self, job_id: UUID, audio_uri: str):
        """Test that NeMo/Parakeet models skip the ALIGN stage (native word timestamps)."""
        parameters = {
            "model_transcribe": "nvidia/parakeet-tdt-1.1b",  # MODEL_REGISTRY key
            "timestamps_granularity": "word",  # Request word timestamps
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # NeMo models have native word timestamps, so no align stage
        assert "align" not in stages
        # Mono pipeline: prepare and transcribe only (2 tasks)
        assert "prepare" in stages
        assert "transcribe" in stages
        # No merge for mono pipeline
        assert "merge" not in stages
        assert len(tasks) == 2

    def test_nemo_with_diarization(self, job_id: UUID, audio_uri: str):
        """Test that NeMo/Parakeet works with diarization (no align, but diarize)."""
        parameters = {
            "model_transcribe": "nvidia/parakeet-tdt-1.1b",
            "speaker_detection": "diarize",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # NeMo should have diarize but NOT align
        assert "diarize" in stages
        assert "align" not in stages

    def test_nemo_per_channel_skips_align(self, job_id: UUID, audio_uri: str):
        """Test that NeMo/Parakeet per-channel mode skips align stages."""
        parameters = {
            "model_transcribe": "nvidia/parakeet-tdt-1.1b",
            "speaker_detection": "per_channel",
            "timestamps_granularity": "word",
            "num_channels": 2,
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Should have per-channel transcribe but NOT align
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" not in stages
        assert "align_ch1" not in stages

    def test_whisper_still_has_align_stage(self, job_id: UUID, audio_uri: str):
        """Test that Whisper (faster-whisper) still uses the ALIGN stage."""
        parameters = {
            "model_transcribe": "Systran/faster-whisper-large-v3-turbo",  # MODEL_REGISTRY key
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Whisper should have align stage (no native word timestamps)
        assert "align" in stages

    def test_nemo_no_merge_in_mono_pipeline(self, job_id: UUID, audio_uri: str):
        """Test that mono pipeline has no merge stage when align is skipped."""
        parameters = {
            "model_transcribe": "nvidia/parakeet-tdt-1.1b",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}

        # No align or merge in mono pipeline with native timestamps
        assert "align" not in task_by_stage
        assert "merge" not in task_by_stage
        # Transcribe depends on prepare
        assert task_by_stage["prepare"].id in task_by_stage["transcribe"].dependencies

    def test_nemo_transcribe_task_uses_nemo_engine_id(
        self, job_id: UUID, audio_uri: str
    ):
        """Test that transcribe task uses NeMo engine_id when a parakeet model is specified.

        M36: MODEL_REGISTRY resolves 'parakeet-tdt-1.1b' to engine_id='nemo' and
        sets loaded_model_id in the task config.
        """
        parameters = {
            "model_transcribe": "nvidia/parakeet-tdt-1.1b",
        }

        tasks = build_task_dag_for_test(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        # M36: engine_id is the logical engine identifier, not the model ID
        assert transcribe_task.engine_id == "nemo"
        # loaded_model_id tells the engine which specific model to load
        assert transcribe_task.config["loaded_model_id"] == "nvidia/parakeet-tdt-1.1b"
