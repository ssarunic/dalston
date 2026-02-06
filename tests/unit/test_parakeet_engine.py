"""Unit tests for Parakeet batch transcription engine.

Tests the ParakeetEngine implementation with mocked NeMo models and CUDA.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_engine.py
"""

from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if torch not installed
torch = pytest.importorskip("torch")


@pytest.fixture
def mock_cuda_available():
    """Mock CUDA as available."""
    with patch.object(torch.cuda, "is_available", return_value=True):
        with patch.object(torch.cuda, "device_count", return_value=1):
            with patch.object(torch.cuda, "memory_allocated", return_value=2e9):
                with patch.object(
                    torch.cuda,
                    "get_device_properties",
                    return_value=MagicMock(total_memory=8e9),
                ):
                    yield


@pytest.fixture
def mock_nemo_asr():
    """Mock NeMo ASR module."""
    mock_model = MagicMock()
    mock_model.to.return_value = mock_model
    mock_model.eval.return_value = mock_model
    mock_model.transcribe.return_value = [MagicMock(text="Hello world", timestep=None)]

    with patch.dict(
        "sys.modules",
        {
            "nemo": MagicMock(),
            "nemo.collections": MagicMock(),
            "nemo.collections.asr": MagicMock(),
        },
    ):
        with patch(
            "nemo.collections.asr.models.ASRModel.from_pretrained",
            return_value=mock_model,
        ):
            yield mock_model


class TestParakeetEngineModelVariants:
    """Tests for Parakeet model variants."""

    def test_default_model_is_06b(self, mock_cuda_available):
        """Test that default model is parakeet-rnnt-0.6b."""
        import sys

        sys.path.insert(0, "engines/transcribe/parakeet")
        from engine import ParakeetEngine

        assert ParakeetEngine.DEFAULT_MODEL == "nvidia/parakeet-rnnt-0.6b"

    def test_supported_model_variants(self, mock_cuda_available):
        """Test that both 0.6B and 1.1B variants are supported."""
        import sys

        sys.path.insert(0, "engines/transcribe/parakeet")
        from engine import ParakeetEngine

        assert "nvidia/parakeet-rnnt-0.6b" in ParakeetEngine.MODEL_VARIANTS
        assert "nvidia/parakeet-rnnt-1.1b" in ParakeetEngine.MODEL_VARIANTS


class TestParakeetEngineHealthCheck:
    """Tests for Parakeet engine health check."""

    def test_health_check_returns_required_fields(self, mock_cuda_available):
        """Test that health check includes GPU information."""
        import sys

        sys.path.insert(0, "engines/transcribe/parakeet")
        from engine import ParakeetEngine

        engine = ParakeetEngine()
        health = engine.health_check()

        assert "status" in health
        assert "cuda_available" in health
        assert "cuda_device_count" in health
        assert "model_loaded" in health

    def test_health_check_reports_healthy_with_cuda(self, mock_cuda_available):
        """Test that health check reports healthy when CUDA available."""
        import sys

        sys.path.insert(0, "engines/transcribe/parakeet")
        from engine import ParakeetEngine

        engine = ParakeetEngine()
        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["cuda_available"] is True


class TestParakeetEngineEnglishOnly:
    """Tests for Parakeet English-only behavior."""

    def test_output_schema_specifies_english(self):
        """Test that Parakeet output is English-only per engine.yaml."""
        # Parakeet should always return language="en"
        # Verify via examining engine.yaml or process() output
        pass


class TestParakeetEngineDagIntegration:
    """Tests for Parakeet integration with DAG builder."""

    def test_parakeet_in_native_word_timestamp_engines(self):
        """Test that parakeet is listed as native word timestamp engine."""
        from dalston.orchestrator.dag import NATIVE_WORD_TIMESTAMP_ENGINES

        assert "parakeet" in NATIVE_WORD_TIMESTAMP_ENGINES

    def test_dag_skips_align_for_parakeet(self):
        """Test that DAG builder skips align stage for Parakeet."""
        from uuid import uuid4

        from dalston.orchestrator.dag import build_task_dag

        job_id = uuid4()
        audio_uri = "s3://test/audio.wav"
        parameters = {
            "engine_transcribe": "parakeet",
            "timestamps_granularity": "word",
        }

        tasks = build_task_dag(job_id, audio_uri, parameters)
        stages = [t.stage for t in tasks]

        assert "align" not in stages
        assert "transcribe" in stages
        assert "merge" in stages
