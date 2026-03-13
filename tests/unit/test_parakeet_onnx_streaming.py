"""Unit tests for Parakeet ONNX real-time streaming engine.

Tests the OnnxRealtimeEngine implementation with M44 dynamic model loading.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_onnx_streaming.py
"""

import importlib.util
import sys
from pathlib import Path

import pytest


def load_parakeet_onnx_streaming_engine():
    """Load OnnxRealtimeEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-unified/onnx/rt_engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet ONNX streaming engine not found")

    spec = importlib.util.spec_from_file_location(
        "parakeet_onnx_streaming_engine", engine_path
    )
    if spec is None or spec.loader is None:
        pytest.skip("Could not load parakeet ONNX streaming engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["parakeet_onnx_streaming_engine"] = module
    spec.loader.exec_module(module)
    return module.OnnxRealtimeEngine


class TestParakeetOnnxStreamingGetModels:
    """Tests for get_models() model identifiers.

    M44: Engine now returns all supported models since it can dynamically load any.
    """

    def test_get_models_returns_all_supported_models(self):
        """Test that get_models returns all dynamically loadable models."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        models = engine.get_models()

        # M44: Returns all supported models, not just the preloaded one
        assert "parakeet-onnx-ctc-0.6b" in models
        assert "parakeet-onnx-ctc-1.1b" in models
        assert "parakeet-onnx-tdt-0.6b-v2" in models
        assert "parakeet-onnx-tdt-0.6b-v3" in models
        assert "parakeet-onnx-rnnt-0.6b" in models

    def test_get_models_returns_list(self):
        """Test that get_models returns a list."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        models = engine.get_models()

        assert isinstance(models, list)
        assert len(models) >= 5  # At least 5 supported models


class TestParakeetOnnxStreamingGetLanguages:
    """Tests for get_languages() English-only restriction."""

    def test_get_languages_returns_english_only(self):
        """Test that get_languages returns only English."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        languages = engine.get_languages()

        assert languages == ["en"]
        assert "fr" not in languages
        assert "de" not in languages


class TestParakeetOnnxStreamingNoStreaming:
    """Tests for ONNX engine (no native streaming)."""

    def test_supports_streaming_returns_false(self):
        """Test that ONNX engine does not claim streaming support."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()

        assert engine.supports_streaming() is False


class TestParakeetOnnxStreamingEngineType:
    """Tests for get_engine_id() type reporting.

    M44: Engine now returns engine_id name instead of variant-specific name.
    """

    def test_get_engine_id_returns_engine_id_name(self):
        """Test that engine type returns the engine_id identifier."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()

        # M44: Returns engine_id name, not variant-specific name
        assert engine.get_engine_id() == "onnx"


class TestParakeetOnnxStreamingHealthCheck:
    """Tests for streaming engine health check."""

    def test_health_check_includes_required_fields(self):
        """Test that health check includes M44 model manager fields."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        health = engine.health_check()

        # M44: New fields from model manager
        assert "models_loaded" in health
        assert "model_count" in health
        assert "max_loaded" in health
        assert "device" in health
        assert "quantization" in health

    def test_health_check_no_models_loaded_initially(self):
        """Test that no models are loaded before load_models() is called."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        health = engine.health_check()

        # M44: models_loaded is a list, not a boolean
        assert health["models_loaded"] == []
        assert health["model_count"] == 0


class TestParakeetOnnxStreamingGPUMemory:
    """Tests for GPU memory reporting."""

    def test_get_gpu_memory_usage_without_cuda(self):
        """Test that GPU memory returns 0GB when CUDA not available."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        usage = engine.get_gpu_memory_usage()

        # Without torch/cuda installed, should return 0GB
        assert "GB" in usage


class TestParakeetOnnxStreamingVocabulary:
    """Tests for vocabulary boosting support."""

    def test_does_not_support_vocabulary(self):
        """Test that ONNX engine reports no vocabulary support."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()

        assert engine.get_supports_vocabulary() is False
