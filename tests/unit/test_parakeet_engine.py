"""Unit tests for Parakeet batch transcription engine.

Tests the ParakeetEngine implementation with mocked NeMo models and CUDA.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_engine.py
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if torch not installed
torch = pytest.importorskip("torch")


def load_parakeet_engine():
    """Load ParakeetEngine from engines directory using importlib."""
    engine_path = Path("engines/transcribe/parakeet/engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet engine not found")

    spec = importlib.util.spec_from_file_location("parakeet_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load parakeet engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["parakeet_engine"] = module
    spec.loader.exec_module(module)
    return module.ParakeetEngine


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

    def test_default_model_size_is_0_6b(self, mock_cuda_available):
        """Test that default model size is 0.6b."""
        ParakeetEngine = load_parakeet_engine()
        assert ParakeetEngine.DEFAULT_MODEL_SIZE == "0.6b"

    def test_supported_model_sizes(self, mock_cuda_available):
        """Test that expected model sizes are supported."""
        ParakeetEngine = load_parakeet_engine()
        # RNNT model sizes
        assert "0.6b" in ParakeetEngine.MODEL_SIZE_MAP
        assert "1.1b" in ParakeetEngine.MODEL_SIZE_MAP
        # Verify NeMo model identifiers
        assert ParakeetEngine.MODEL_SIZE_MAP["0.6b"] == "nvidia/parakeet-rnnt-0.6b"
        assert ParakeetEngine.MODEL_SIZE_MAP["1.1b"] == "nvidia/parakeet-rnnt-1.1b"


class TestParakeetEngineHealthCheck:
    """Tests for Parakeet engine health check."""

    def test_health_check_returns_required_fields(self, mock_cuda_available):
        """Test that health check includes GPU information."""
        ParakeetEngine = load_parakeet_engine()
        engine = ParakeetEngine()
        health = engine.health_check()

        assert "status" in health
        assert "cuda_available" in health
        assert "cuda_device_count" in health
        assert "model_loaded" in health

    def test_health_check_reports_healthy_with_cuda(self, mock_cuda_available):
        """Test that health check reports healthy when CUDA available."""
        ParakeetEngine = load_parakeet_engine()
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
