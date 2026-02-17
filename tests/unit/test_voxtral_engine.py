"""Unit tests for Voxtral transcription engine.

Tests engine logic without loading the actual model weights.
"""

# Check if transformers is available for integration-style tests
import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None


class TestVoxtralEngine:
    """Test VoxtralEngine without loading actual model."""

    @pytest.fixture
    def mock_torch(self):
        """Mock torch to avoid CUDA checks."""
        with patch.dict(os.environ, {"DEVICE": "cpu", "MODEL_SIZE": "mini-3b"}):
            with patch("torch.cuda.is_available", return_value=False):
                with patch("torch.cuda.device_count", return_value=0):
                    yield

    @pytest.fixture
    def engine(self, mock_torch):
        """Create engine instance with mocked dependencies."""
        from engines.transcribe.voxtral.engine import VoxtralEngine

        engine = VoxtralEngine()
        return engine

    def test_engine_init_cpu_fallback(self, engine):
        """Engine should fall back to CPU when CUDA unavailable."""
        assert engine._device == "cpu"
        assert engine._model_size == "mini-3b"
        assert engine._hf_model_id == "mistralai/Voxtral-Mini-3B-2507"

    def test_supported_languages(self, engine):
        """Engine should support 8 languages."""
        expected = ["en", "es", "fr", "pt", "hi", "de", "nl", "it"]
        assert engine.SUPPORTED_LANGUAGES == expected

    def test_model_size_map(self, engine):
        """Engine should have correct model mappings."""
        assert "mini-3b" in engine.MODEL_SIZE_MAP
        assert "small-24b" in engine.MODEL_SIZE_MAP
        assert engine.MODEL_SIZE_MAP["mini-3b"] == "mistralai/Voxtral-Mini-3B-2507"
        assert engine.MODEL_SIZE_MAP["small-24b"] == "mistralai/Voxtral-Small-24B-2507"

    def test_get_capabilities(self, engine):
        """Engine capabilities should be correct."""
        caps = engine.get_capabilities()

        assert caps.engine_id == "voxtral-mini-3b"
        assert caps.version == "1.0.0"
        assert caps.stages == ["transcribe"]
        assert caps.languages == ["en", "es", "fr", "pt", "hi", "de", "nl", "it"]
        assert caps.supports_word_timestamps is False
        assert caps.supports_streaming is False
        assert caps.gpu_required is True
        assert caps.gpu_vram_mb == 9500

    def test_health_check_no_model(self, engine):
        """Health check should work without model loaded."""
        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["device"] == "cpu"
        assert health["model_loaded"] is False
        assert health["cuda_available"] is False

    @pytest.mark.skipif(not HAS_TRANSFORMERS, reason="transformers not installed")
    @patch("transformers.VoxtralForConditionalGeneration")
    @patch("transformers.AutoProcessor")
    def test_load_model(self, mock_processor_cls, mock_model_cls, engine):
        """Model loading should use correct classes."""
        mock_processor = MagicMock()
        mock_model = MagicMock()
        mock_processor_cls.from_pretrained.return_value = mock_processor
        mock_model_cls.from_pretrained.return_value = mock_model

        engine._load_model("mistralai/Voxtral-Mini-3B-2507")

        mock_processor_cls.from_pretrained.assert_called_once_with(
            "mistralai/Voxtral-Mini-3B-2507"
        )
        mock_model_cls.from_pretrained.assert_called_once()
        assert engine._model is not None
        assert engine._processor is not None

    @pytest.mark.skipif(not HAS_TRANSFORMERS, reason="transformers not installed")
    @patch("transformers.VoxtralForConditionalGeneration")
    @patch("transformers.AutoProcessor")
    def test_process_mocked(self, mock_processor_cls, mock_model_cls, engine, tmp_path):
        """Process should return valid TranscribeOutput."""
        from dalston.engine_sdk import TaskInput

        # Setup mocks
        mock_processor = MagicMock()
        mock_model = MagicMock()
        mock_processor_cls.from_pretrained.return_value = mock_processor
        mock_model_cls.from_pretrained.return_value = mock_model

        # Mock the transcription pipeline
        mock_inputs = MagicMock()
        mock_inputs.input_ids = MagicMock()
        mock_inputs.input_ids.shape = [1, 100]
        mock_processor.apply_transcription_request.return_value = mock_inputs

        mock_outputs = MagicMock()
        mock_model.generate.return_value = mock_outputs
        mock_processor.batch_decode.return_value = [
            "Hello, this is a test transcription."
        ]

        # Create a dummy audio file
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 100)  # Minimal WAV header

        # Create TaskInput
        task_input = TaskInput(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            previous_outputs={},
            config={"language": "en"},
            stage="transcribe",
        )

        # Process
        result = engine.process(task_input)

        # Verify result
        assert result.data is not None
        assert result.data.text == "Hello, this is a test transcription."
        assert result.data.language == "en"
        assert result.data.engine_id == "voxtral-mini-3b"
        assert len(result.data.segments) == 1


class TestVoxtralEngineEnvironment:
    """Test engine environment variable handling."""

    def test_unknown_model_size_falls_back(self):
        """Unknown MODEL_SIZE should fall back to default."""
        with patch.dict(os.environ, {"MODEL_SIZE": "unknown", "DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=False):
                from engines.transcribe.voxtral.engine import VoxtralEngine

                engine = VoxtralEngine()
                assert engine._model_size == "mini-3b"

    def test_explicit_cpu_device(self):
        """Explicit DEVICE=cpu should use CPU."""
        with patch.dict(os.environ, {"DEVICE": "cpu", "MODEL_SIZE": "mini-3b"}):
            with patch("torch.cuda.is_available", return_value=True):
                from engines.transcribe.voxtral.engine import VoxtralEngine

                engine = VoxtralEngine()
                assert engine._device == "cpu"
