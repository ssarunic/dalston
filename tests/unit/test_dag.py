"""Unit tests for DAG builder with model selection.

These tests require the orchestrator extras (asyncpg, sqlalchemy).
Run with: uv run --extra dev --extra orchestrator pytest tests/unit/test_dag.py
"""

from uuid import UUID, uuid4

import pytest

from dalston.orchestrator.dag import (
    DEFAULT_ENGINES,
    DEFAULT_TRANSCRIBE_CONFIG,
    MODEL_REGISTRY,
    NATIVE_WORD_TIMESTAMP_ENGINES,
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
        """Test that default model config is used when no model specified.

        M36: With runtime model management, the default engine (faster-whisper-base)
        is resolved to its runtime (faster-whisper) and runtime_model_id is set.
        """
        tasks = build_task_dag(job_id, audio_uri, {})

        # Find transcribe task
        transcribe_task = next(t for t in tasks if t.stage == "transcribe")

        # M36: engine_id should be the runtime, not the model-specific engine
        default_engine = DEFAULT_ENGINES["transcribe"]
        model_info = MODEL_REGISTRY.get(default_engine)
        if model_info:
            assert transcribe_task.engine_id == model_info["runtime"]
            assert (
                transcribe_task.config["runtime_model_id"]
                == model_info["runtime_model_id"]
            )
        else:
            assert transcribe_task.engine_id == default_engine
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
        assert (
            transcribe_task.config["beam_size"]
            == DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
        )
        assert (
            transcribe_task.config["vad_filter"]
            == DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]
        )

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


class TestBuildTaskDagParakeet:
    """Tests for Parakeet engine DAG behavior (M21)."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_parakeet_in_native_word_timestamp_engines(self):
        """Test that parakeet is listed in native word timestamp engines."""
        assert "parakeet" in NATIVE_WORD_TIMESTAMP_ENGINES

    def test_parakeet_skips_align_stage(self, job_id: UUID, audio_uri: str):
        """Test that Parakeet engine skips the ALIGN stage."""
        parameters = {
            "engine_transcribe": "parakeet",
            "timestamps_granularity": "word",  # Request word timestamps
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Parakeet should NOT have align stage
        assert "align" not in stages
        # But should have other stages
        assert "prepare" in stages
        assert "transcribe" in stages
        assert "merge" in stages

    def test_parakeet_with_diarization(self, job_id: UUID, audio_uri: str):
        """Test that Parakeet works with diarization (no align, but diarize)."""
        parameters = {
            "engine_transcribe": "parakeet",
            "speaker_detection": "diarize",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Parakeet should have diarize but NOT align
        assert "diarize" in stages
        assert "align" not in stages

    def test_parakeet_per_channel_skips_align(self, job_id: UUID, audio_uri: str):
        """Test that Parakeet per-channel mode skips align stages."""
        parameters = {
            "engine_transcribe": "parakeet",
            "speaker_detection": "per_channel",
            "timestamps_granularity": "word",
            "num_channels": 2,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Should have per-channel transcribe but NOT align
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" not in stages
        assert "align_ch1" not in stages

    def test_whisper_still_has_align_stage(self, job_id: UUID, audio_uri: str):
        """Test that Whisper (faster-whisper) still uses the ALIGN stage."""
        parameters = {
            "engine_transcribe": "faster-whisper",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Whisper should have align stage
        assert "align" in stages

    def test_parakeet_merge_dependencies_correct(self, job_id: UUID, audio_uri: str):
        """Test that merge dependencies are correct when align is skipped."""
        parameters = {
            "engine_transcribe": "parakeet",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        merge_deps = task_by_stage["merge"].dependencies

        # Merge should depend on prepare and transcribe (but NOT align since it's skipped)
        assert task_by_stage["prepare"].id in merge_deps
        assert task_by_stage["transcribe"].id in merge_deps
        assert len([t for t in tasks if t.stage == "align"]) == 0

    def test_parakeet_transcribe_task_uses_parakeet_engine(
        self, job_id: UUID, audio_uri: str
    ):
        """Test that transcribe task uses Parakeet engine when specified."""
        parameters = {
            "engine_transcribe": "parakeet",
            "transcribe_config": {
                "model": "nvidia/parakeet-rnnt-0.6b",
            },
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        transcribe_task = next(t for t in tasks if t.stage == "transcribe")
        assert transcribe_task.engine_id == "parakeet"
        assert transcribe_task.config["model"] == "nvidia/parakeet-rnnt-0.6b"


class TestBuildTaskDagPerChannelPII:
    """Tests for per-channel PII detection and audio redaction."""

    @pytest.fixture
    def job_id(self) -> UUID:
        return uuid4()

    @pytest.fixture
    def audio_uri(self) -> str:
        return "s3://test-bucket/audio/test.wav"

    def test_per_channel_pii_stages_created(self, job_id: UUID, audio_uri: str):
        """Test that per-channel PII detection stages are created."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "pii_detection": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Should have per-channel PII detect stages
        assert "pii_detect_ch0" in stages
        assert "pii_detect_ch1" in stages
        # Should NOT have single pii_detect stage
        assert "pii_detect" not in stages

    def test_per_channel_audio_redact_stages_created(
        self, job_id: UUID, audio_uri: str
    ):
        """Test that per-channel audio redaction stages are created."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]
        # Should have per-channel audio redact stages
        assert "audio_redact_ch0" in stages
        assert "audio_redact_ch1" in stages
        # Should NOT have single audio_redact stage
        assert "audio_redact" not in stages

    def test_per_channel_pii_dependencies_correct(self, job_id: UUID, audio_uri: str):
        """Test that PII detect depends on align (or transcribe if no align)."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "timestamps_granularity": "word",
            "pii_detection": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}

        # pii_detect_ch0 should depend on align_ch0
        assert (
            task_by_stage["align_ch0"].id
            in task_by_stage["pii_detect_ch0"].dependencies
        )
        # pii_detect_ch1 should depend on align_ch1
        assert (
            task_by_stage["align_ch1"].id
            in task_by_stage["pii_detect_ch1"].dependencies
        )

    def test_per_channel_audio_redact_dependencies_correct(
        self, job_id: UUID, audio_uri: str
    ):
        """Test that audio redact depends on pii detect."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}

        # audio_redact_ch0 should depend on pii_detect_ch0
        assert (
            task_by_stage["pii_detect_ch0"].id
            in task_by_stage["audio_redact_ch0"].dependencies
        )
        # audio_redact_ch1 should depend on pii_detect_ch1
        assert (
            task_by_stage["pii_detect_ch1"].id
            in task_by_stage["audio_redact_ch1"].dependencies
        )

    def test_per_channel_merge_depends_on_all_stages(
        self, job_id: UUID, audio_uri: str
    ):
        """Test that merge depends on all per-channel stages."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "timestamps_granularity": "word",
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        task_by_stage = {t.stage: t for t in tasks}
        merge_deps = task_by_stage["merge"].dependencies

        # Merge should depend on prepare and all per-channel tasks
        assert task_by_stage["prepare"].id in merge_deps
        assert task_by_stage["transcribe_ch0"].id in merge_deps
        assert task_by_stage["transcribe_ch1"].id in merge_deps
        assert task_by_stage["align_ch0"].id in merge_deps
        assert task_by_stage["align_ch1"].id in merge_deps
        assert task_by_stage["pii_detect_ch0"].id in merge_deps
        assert task_by_stage["pii_detect_ch1"].id in merge_deps
        assert task_by_stage["audio_redact_ch0"].id in merge_deps
        assert task_by_stage["audio_redact_ch1"].id in merge_deps

    def test_per_channel_merge_config_has_pii_flags(self, job_id: UUID, audio_uri: str):
        """Test that merge config includes PII flags."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        merge_task = next(t for t in tasks if t.stage == "merge")

        assert merge_task.config.get("pii_detection") is True
        assert merge_task.config.get("redact_pii_audio") is True

    def test_per_channel_full_pipeline_task_count(self, job_id: UUID, audio_uri: str):
        """Test full per-channel pipeline has expected task count."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "timestamps_granularity": "word",
            "pii_detection": True,
            "redact_pii_audio": True,
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        # Expected stages:
        # - prepare (1)
        # - transcribe_ch0, transcribe_ch1 (2)
        # - align_ch0, align_ch1 (2)
        # - pii_detect_ch0, pii_detect_ch1 (2)
        # - audio_redact_ch0, audio_redact_ch1 (2)
        # - merge (1)
        # Total: 10 tasks
        assert len(tasks) == 10

    def test_per_channel_pii_no_redaction_task_count(
        self, job_id: UUID, audio_uri: str
    ):
        """Test per-channel with PII detection but no redaction."""
        parameters = {
            "speaker_detection": "per_channel",
            "num_channels": 2,
            "timestamps_granularity": "word",
            "pii_detection": True,
            "redact_pii_audio": False,  # No audio redaction
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)

        stages = [t.stage for t in tasks]

        # Should have pii_detect but NOT audio_redact
        assert "pii_detect_ch0" in stages
        assert "pii_detect_ch1" in stages
        assert "audio_redact_ch0" not in stages
        assert "audio_redact_ch1" not in stages

        # Expected: prepare, transcribe_ch0/1, align_ch0/1, pii_detect_ch0/1, merge
        # Total: 8 tasks
        assert len(tasks) == 8
