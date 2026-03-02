"""Unit tests for Parakeet ONNX real-time streaming engine.

Tests the ParakeetOnnxStreamingEngine implementation with mocked onnx-asr models.
Run with: uv run --extra dev pytest tests/unit/test_parakeet_onnx_streaming.py
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def load_parakeet_onnx_streaming_engine():
    """Load ParakeetOnnxStreamingEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-rt/parakeet-onnx/engine.py")
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
    return module.ParakeetOnnxStreamingEngine


class TestParakeetOnnxStreamingGetModels:
    """Tests for get_models() model identifiers."""

    def test_get_models_returns_onnx_model_name(self):
        """Test that get_models returns the ONNX-specific model identifier."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        models = engine.get_models()

        # Default variant is ctc-0.6b
        assert models == ["parakeet-onnx-ctc-0.6b"]

    def test_get_models_with_1_1b_variant(self):
        """Test that get_models returns correct name for 1.1b variant."""
        with patch.dict("os.environ", {"DALSTON_MODEL_VARIANT": "ctc-1.1b"}):
            Engine = load_parakeet_onnx_streaming_engine()
            engine = Engine()
            models = engine.get_models()

            assert models == ["parakeet-onnx-ctc-1.1b"]


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
    """Tests for CTC-based engine (no native streaming)."""

    def test_supports_streaming_returns_false(self):
        """Test that CTC engine does not claim streaming support."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()

        assert engine.supports_streaming() is False


class TestParakeetOnnxStreamingEngineType:
    """Tests for get_engine() type reporting."""

    def test_get_engine_returns_onnx_type(self):
        """Test that engine type includes onnx identifier."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()

        assert engine.get_engine() == "parakeet-onnx-ctc-0.6b"

    def test_get_engine_with_1_1b_variant(self):
        """Test that engine type reflects 1.1b variant."""
        with patch.dict("os.environ", {"DALSTON_MODEL_VARIANT": "ctc-1.1b"}):
            Engine = load_parakeet_onnx_streaming_engine()
            engine = Engine()

            assert engine.get_engine() == "parakeet-onnx-ctc-1.1b"


class TestParakeetOnnxStreamingHealthCheck:
    """Tests for streaming engine health check."""

    def test_health_check_includes_required_fields(self):
        """Test that health check includes ONNX-specific fields."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        health = engine.health_check()

        assert "model_loaded" in health
        assert "model_name" in health
        assert "device" in health
        assert "quantization" in health

    def test_health_check_model_not_loaded_initially(self):
        """Test that model is not loaded before load_models() is called."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        health = engine.health_check()

        assert health["model_loaded"] is False
        assert health["model_name"] is None


class TestParakeetOnnxStreamingGPUMemory:
    """Tests for GPU memory reporting."""

    def test_get_gpu_memory_usage_without_cuda(self):
        """Test that GPU memory returns 0GB when CUDA not available."""
        Engine = load_parakeet_onnx_streaming_engine()
        engine = Engine()
        usage = engine.get_gpu_memory_usage()

        # Without torch/cuda installed, should return 0GB
        assert "GB" in usage


class TestParakeetOnnxStreamingVariantValidation:
    """Tests for model variant selection and validation."""

    def test_unknown_variant_falls_back_to_default(self):
        """Test that unknown variant falls back to default with warning."""
        with patch.dict("os.environ", {"DALSTON_MODEL_VARIANT": "unknown-variant"}):
            Engine = load_parakeet_onnx_streaming_engine()
            engine = Engine()

            # Should fall back to default
            assert engine._model_variant == "ctc-0.6b"

    def test_valid_variants(self):
        """Test that valid variants are accepted."""
        for variant in ("ctc-0.6b", "ctc-1.1b"):
            with patch.dict("os.environ", {"DALSTON_MODEL_VARIANT": variant}):
                Engine = load_parakeet_onnx_streaming_engine()
                engine = Engine()
                assert engine._model_variant == variant
