"""Unit tests for Parakeet real-time streaming engine.

Tests the ParakeetStreamingEngine implementation with mocked NeMo models and CUDA.
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
    """Load ParakeetStreamingEngine from engines directory using importlib."""
    engine_path = Path("engines/realtime/parakeet-streaming/engine.py")
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
    return module.ParakeetStreamingEngine


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
    """Tests for get_models() compatibility aliases."""

    def test_get_models_returns_compatibility_aliases(self, mock_cuda_available):
        """Test that get_models returns parakeet, fast, and accurate."""
        ParakeetStreamingEngine = load_parakeet_streaming_engine()
        engine = ParakeetStreamingEngine()
        models = engine.get_models()

        assert "parakeet" in models
        assert "fast" in models
        assert "accurate" in models


class TestParakeetStreamingEngineGetLanguages:
    """Tests for get_languages() English-only restriction."""

    def test_get_languages_returns_english_only(self, mock_cuda_available):
        """Test that get_languages returns only English."""
        ParakeetStreamingEngine = load_parakeet_streaming_engine()
        engine = ParakeetStreamingEngine()
        languages = engine.get_languages()

        assert languages == ["en"]
        assert "fr" not in languages
        assert "de" not in languages


class TestParakeetStreamingEngineHealthCheck:
    """Tests for streaming engine health check."""

    def test_health_check_includes_required_fields(self, mock_cuda_available):
        """Test that health check includes GPU and model info."""
        ParakeetStreamingEngine = load_parakeet_streaming_engine()
        engine = ParakeetStreamingEngine()
        health = engine.health_check()

        assert "cuda_available" in health
        assert "chunk_size_ms" in health
        assert "model_loaded" in health


class TestParakeetStreamingEngineGPUMemory:
    """Tests for GPU memory reporting."""

    def test_get_gpu_memory_usage_format(self, mock_cuda_available):
        """Test that GPU memory usage is returned in correct format."""
        ParakeetStreamingEngine = load_parakeet_streaming_engine()
        engine = ParakeetStreamingEngine()
        usage = engine.get_gpu_memory_usage()

        assert "GB" in usage
