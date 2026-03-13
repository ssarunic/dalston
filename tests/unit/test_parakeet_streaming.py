"""Unit tests for Parakeet real-time streaming engine.

Tests the NemoRealtimeEngine implementation with mocked NeMo models and CUDA.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_streaming.py
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if torch not installed
torch = pytest.importorskip("torch")


def load_parakeet_streaming_engine():
    """Load NemoRealtimeEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-unified/nemo/rt_engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet streaming engine not found")

    spec = importlib.util.spec_from_file_location(
        "parakeet_streaming_engine", engine_path
    )
    if spec is None or spec.loader is None:
        pytest.skip("Could not load parakeet streaming engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["parakeet_streaming_engine"] = module
    spec.loader.exec_module(module)
    return module.NemoRealtimeEngine


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


class TestParakeetStreamingEngineGetModels:
    """Tests for get_models() - M44 dynamic model loading."""

    def test_get_models_returns_all_supported_models(self, mock_cuda_available):
        """Test that get_models returns all dynamically loadable models.

        M44: NeMo engine_id container can load any supported Parakeet model on-demand.
        """
        NemoRealtimeEngine = load_parakeet_streaming_engine()
        engine = NemoRealtimeEngine()
        models = engine.get_models()

        # M44: Returns all models the NeMoModelManager can load
        assert "parakeet-rnnt-0.6b" in models
        assert "parakeet-rnnt-1.1b" in models
        assert "parakeet-ctc-0.6b" in models
        assert "parakeet-ctc-1.1b" in models
        assert "parakeet-tdt-0.6b-v3" in models
        assert "parakeet-tdt-1.1b" in models
        assert len(models) == 6


class TestParakeetStreamingEngineGetLanguages:
    """Tests for get_languages() English-only restriction."""

    def test_get_languages_returns_english_only(self, mock_cuda_available):
        """Test that get_languages returns only English."""
        NemoRealtimeEngine = load_parakeet_streaming_engine()
        engine = NemoRealtimeEngine()
        languages = engine.get_languages()

        assert languages == ["en"]
        assert "fr" not in languages
        assert "de" not in languages


class TestParakeetStreamingEngineHealthCheck:
    """Tests for streaming engine health check - M44 dynamic model loading."""

    def test_health_check_includes_required_fields(self, mock_cuda_available):
        """Test that health check includes GPU and model manager info.

        M44: Health check reports model manager state instead of static model.
        """
        NemoRealtimeEngine = load_parakeet_streaming_engine()
        engine = NemoRealtimeEngine()
        health = engine.health_check()

        # M44: Dynamic model manager fields
        assert "cuda_available" in health
        assert "models_loaded" in health
        assert "model_count" in health
        assert "max_loaded" in health
        assert "device" in health


class TestParakeetStreamingEngineGPUMemory:
    """Tests for GPU memory reporting."""

    def test_get_gpu_memory_usage_format(self, mock_cuda_available):
        """Test that GPU memory usage is returned in correct format."""
        NemoRealtimeEngine = load_parakeet_streaming_engine()
        engine = NemoRealtimeEngine()
        usage = engine.get_gpu_memory_usage()

        assert "GB" in usage
