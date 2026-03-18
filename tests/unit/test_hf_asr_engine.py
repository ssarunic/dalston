"""Unit tests for HuggingFace Transformers ASR engine.

Tests engine logic and output normalization without loading actual model weights.
"""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.managers.hf_transformers import HFTransformersModelManager
from dalston.engine_sdk.types import TaskRequest

HAS_TORCH = importlib.util.find_spec("torch") is not None


def _ctx(task_request: TaskRequest) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="hf-asr",
        instance="test-instance",
        task_id=task_request.task_id,
        job_id=task_request.job_id,
        stage=task_request.stage,
    )


def load_hf_asr_engine():
    """Load HfAsrBatchEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/hf-asr/batch_engine.py")
    if not engine_path.exists():
        pytest.skip("HF-ASR engine not found")

    spec = importlib.util.spec_from_file_location("hf_asr_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load hf-asr engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["hf_asr_engine"] = module
    spec.loader.exec_module(module)
    return module.HfAsrBatchEngine


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestHFASREngine:
    """Test HfAsrBatchEngine without loading actual model."""

    @pytest.fixture(autouse=True)
    def _clear_module_cache(self):
        """Clear cached module before and after each test."""
        sys.modules.pop("hf_asr_engine", None)
        yield
        sys.modules.pop("hf_asr_engine", None)

    @pytest.fixture
    def mock_torch(self):
        """Mock torch to avoid CUDA checks."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=False):
                with patch("torch.cuda.device_count", return_value=0):
                    yield

    @pytest.fixture
    def engine(self, mock_torch):
        """Create engine instance with mocked dependencies."""
        HfAsrBatchEngine = load_hf_asr_engine()
        return HfAsrBatchEngine()

    def test_engine_init_cpu_fallback(self, engine):
        """Engine should fall back to CPU when CUDA unavailable."""
        assert engine._device == "cpu"

    def test_default_model_id(self, engine):
        """Engine should default to whisper-large-v3."""
        assert engine._default_model_id == "openai/whisper-large-v3"

    def test_custom_default_model_from_env(self, mock_torch):
        """Engine should respect DALSTON_DEFAULT_MODEL_ID env var."""
        with patch.dict(
            os.environ,
            {"DALSTON_DEFAULT_MODEL_ID": "facebook/wav2vec2-large-960h"},
        ):
            HfAsrBatchEngine = load_hf_asr_engine()
            engine = HfAsrBatchEngine()
            assert engine._default_model_id == "facebook/wav2vec2-large-960h"

    def test_engine_id_from_env(self):
        """Engine should respect DALSTON_ENGINE_ID env var."""
        with patch.dict(
            os.environ, {"DALSTON_ENGINE_ID": "custom-hf-asr", "DALSTON_DEVICE": "cpu"}
        ):
            with patch("torch.cuda.is_available", return_value=False):
                HfAsrBatchEngine = load_hf_asr_engine()
                engine = HfAsrBatchEngine()
                assert engine._engine_id == "custom-hf-asr"

    def test_health_check(self, engine):
        """Health check should return healthy status."""
        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["engine_id"] == "hf-asr"
        assert health["device"] == "cpu"
        assert health["cuda_available"] is False

    def test_health_check_includes_model_stats(self, engine):
        """Health check should include model manager stats."""
        health = engine.health_check()

        assert "model_manager" in health
        assert health["model_manager"]["model_count"] == 0
        assert health["model_manager"]["max_loaded"] == 2

    def test_shutdown_calls_manager_shutdown(self, engine):
        """Shutdown should call manager shutdown."""
        engine._manager.shutdown = MagicMock()
        engine.shutdown()
        engine._manager.shutdown.assert_called_once()


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestHFASREngineNormalizeOutput:
    """Test output normalization for different model architectures."""

    @pytest.fixture
    def engine(self):
        """Create engine instance for normalization tests."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=False):
                HfAsrBatchEngine = load_hf_asr_engine()
                return HfAsrBatchEngine()

    def test_normalize_whisper_output_with_word_timestamps(self, engine):
        """Whisper output with word-level chunks should produce word timestamps."""
        result = {
            "text": "Hello world",
            "chunks": [
                {"text": "Hello", "timestamp": (0.0, 0.5)},
                {"text": "world", "timestamp": (0.5, 1.0)},
            ],
        }

        output = engine._normalize_output(result, "openai/whisper-large-v3", "en", None)

        assert output.text == "Hello world"
        assert len(output.segments) == 1
        assert output.segments[0].words is not None
        assert len(output.segments[0].words) == 2
        assert output.segments[0].words[0].text == "Hello"
        assert output.segments[0].words[0].start == 0.0
        assert output.segments[0].words[0].end == 0.5
        assert output.segments[0].words[1].text == "world"
        assert output.segments[0].words[1].start == 0.5
        assert output.segments[0].words[1].end == 1.0
        assert output.timestamp_granularity.value == "word"
        assert output.alignment_method.value == "attention"

    def test_normalize_wav2vec2_output_no_timestamps(self, engine):
        """Wav2Vec2 output without chunks should produce no timestamps."""
        result = {
            "text": "HELLO WORLD",
        }

        output = engine._normalize_output(
            result, "facebook/wav2vec2-large-960h", "en", None
        )

        assert output.text == "HELLO WORLD"
        assert len(output.segments) == 1
        assert output.segments[0].words is None
        assert output.segments[0].start == 0.0
        assert output.segments[0].end == 0.0
        assert output.timestamp_granularity.value == "segment"
        assert output.alignment_method.value == "unknown"

    def test_normalize_output_with_none_timestamps(self, engine):
        """Chunks with None timestamps should default to 0.0."""
        result = {
            "text": "Test",
            "chunks": [
                {"text": "Test", "timestamp": (None, None)},
            ],
        }

        output = engine._normalize_output(result, "test/model", None, None)

        assert output.segments[0].words[0].start == 0.0
        assert output.segments[0].words[0].end == 0.0

    def test_normalize_output_empty_text(self, engine):
        """Empty text should produce empty output."""
        result = {"text": ""}

        output = engine._normalize_output(result, "test/model", None, None)

        assert output.text == ""
        assert len(output.segments) == 1
        assert output.segments[0].text == ""

    def test_normalize_output_language_auto(self, engine):
        """None language should be reported as 'auto'."""
        result = {"text": "test"}

        output = engine._normalize_output(result, "test/model", None, None)

        assert output.language == "auto"

    def test_normalize_output_explicit_language(self, engine):
        """Explicit language should be preserved."""
        result = {"text": "test"}

        output = engine._normalize_output(result, "test/model", "fr", None)

        assert output.language == "fr"

    def test_normalize_output_channel(self, engine):
        """Channel should be passed through."""
        result = {"text": "test"}

        output = engine._normalize_output(result, "test/model", "en", 0)

        assert output.channel == 0

    def test_normalize_output_engine_id(self, engine):
        """Engine ID should be set."""
        result = {"text": "test"}

        output = engine._normalize_output(result, "test/model", "en", None)

        assert output.engine_id == "hf-asr"

    def test_normalize_output_skips_empty_chunks(self, engine):
        """Empty chunk text should be skipped."""
        result = {
            "text": "Hello",
            "chunks": [
                {"text": "", "timestamp": (0.0, 0.1)},
                {"text": "Hello", "timestamp": (0.1, 0.5)},
            ],
        }

        output = engine._normalize_output(result, "test/model", "en", None)

        assert len(output.segments[0].words) == 1
        assert output.segments[0].words[0].text == "Hello"


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestHFASREngineProcess:
    """Test the process method with mocked pipeline."""

    @pytest.fixture
    def engine(self):
        """Create engine instance."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=False):
                HfAsrBatchEngine = load_hf_asr_engine()
                return HfAsrBatchEngine()

    def test_process_uses_loaded_model_id(self, engine, tmp_path):
        """Process should use loaded_model_id from config."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_pipe = MagicMock()
        mock_pipe.return_value = {"text": "hello", "chunks": []}
        engine._manager.acquire = MagicMock(return_value=mock_pipe)
        engine._manager.release = MagicMock()

        task_request = TaskRequest(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            config={"loaded_model_id": "facebook/wav2vec2-large-960h"},
        )

        engine.process(task_request, _ctx(task_request))

        engine._manager.acquire.assert_called_once_with("facebook/wav2vec2-large-960h")
        engine._manager.release.assert_called_once_with("facebook/wav2vec2-large-960h")

    def test_process_uses_default_model(self, engine, tmp_path):
        """Process should use default model when loaded_model_id not set."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_pipe = MagicMock()
        mock_pipe.return_value = {"text": "hello", "chunks": []}
        engine._manager.acquire = MagicMock(return_value=mock_pipe)
        engine._manager.release = MagicMock()

        task_request = TaskRequest(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            config={},
        )

        engine.process(task_request, _ctx(task_request))

        engine._manager.acquire.assert_called_once_with("openai/whisper-large-v3")

    def test_process_passes_language(self, engine, tmp_path):
        """Process should pass language to generate_kwargs."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_pipe = MagicMock()
        mock_pipe.return_value = {"text": "bonjour", "chunks": []}
        engine._manager.acquire = MagicMock(return_value=mock_pipe)
        engine._manager.release = MagicMock()

        task_request = TaskRequest(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            config={"language": "fr"},
        )

        engine.process(task_request, _ctx(task_request))

        # Verify pipeline was called with language
        call_kwargs = mock_pipe.call_args[1]
        assert call_kwargs["generate_kwargs"]["language"] == "fr"

    def test_process_auto_language_omits_generate_kwargs(self, engine, tmp_path):
        """Auto language should not pass language to generate_kwargs."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_pipe = MagicMock()
        mock_pipe.return_value = {"text": "hello", "chunks": []}
        engine._manager.acquire = MagicMock(return_value=mock_pipe)
        engine._manager.release = MagicMock()

        task_request = TaskRequest(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            config={"language": "auto"},
        )

        engine.process(task_request, _ctx(task_request))

        call_kwargs = mock_pipe.call_args[1]
        assert "generate_kwargs" not in call_kwargs

    def test_process_returns_task_output(self, engine, tmp_path):
        """Process should return valid TaskResponse."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_pipe = MagicMock()
        mock_pipe.return_value = {
            "text": "Hello world",
            "chunks": [
                {"text": "Hello", "timestamp": (0.0, 0.5)},
                {"text": "world", "timestamp": (0.5, 1.0)},
            ],
        }
        engine._manager.acquire = MagicMock(return_value=mock_pipe)
        engine._manager.release = MagicMock()

        task_request = TaskRequest(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            config={},
        )

        result = engine.process(task_request, _ctx(task_request))

        assert result.data is not None
        output_dict = result.to_dict()
        assert output_dict["text"] == "Hello world"
        assert len(output_dict["segments"]) == 1
        assert output_dict["engine_id"] == "hf-asr"

    def test_process_releases_model_on_error(self, engine, tmp_path):
        """Process should release model even if pipeline raises."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_pipe = MagicMock()
        mock_pipe.side_effect = RuntimeError("inference failed")
        engine._manager.acquire = MagicMock(return_value=mock_pipe)
        engine._manager.release = MagicMock()

        task_request = TaskRequest(
            task_id="test-task",
            job_id="test-job",
            audio_path=audio_file,
            config={},
        )

        with pytest.raises(RuntimeError, match="inference failed"):
            engine.process(task_request, _ctx(task_request))

        engine._manager.release.assert_called_once_with("openai/whisper-large-v3")


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestHFASREngineEnvironment:
    """Test engine environment variable handling."""

    def test_explicit_cpu_device(self):
        """Explicit DEVICE=cpu should use CPU even when CUDA available."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=True):
                HfAsrBatchEngine = load_hf_asr_engine()
                engine = HfAsrBatchEngine()
                assert engine._device == "cpu"

    def test_cuda_device_when_available(self):
        """Should use CUDA when available and not explicitly set to CPU."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": ""}):
            with patch("torch.cuda.is_available", return_value=True):
                with patch("torch.cuda.device_count", return_value=1):
                    HfAsrBatchEngine = load_hf_asr_engine()
                    engine = HfAsrBatchEngine()
                    assert engine._device == "cuda"

    def test_cuda_requested_but_unavailable_raises(self):
        """Requesting CUDA when unavailable should raise RuntimeError."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "cuda"}):
            with patch("torch.cuda.is_available", return_value=False):
                HfAsrBatchEngine = load_hf_asr_engine()
                with pytest.raises(RuntimeError, match="CUDA is not available"):
                    HfAsrBatchEngine()

    def test_unknown_device_raises(self):
        """Unknown device name should raise ValueError."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "tpu"}):
            with patch("torch.cuda.is_available", return_value=False):
                HfAsrBatchEngine = load_hf_asr_engine()
                with pytest.raises(ValueError, match="Unknown DALSTON_DEVICE"):
                    HfAsrBatchEngine()


class TestHFTransformersModelManager:
    """Test HFTransformersModelManager without loading models."""

    @pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
    def test_init(self):
        """Manager should initialize with device and dtype."""
        import torch

        manager = HFTransformersModelManager(
            device="cpu",
            torch_dtype=torch.float32,
            ttl_seconds=60,
            max_loaded=1,
        )

        assert manager.device == "cpu"
        assert manager.torch_dtype == torch.float32
        manager.shutdown()

    @pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
    def test_load_model_calls_pipeline(self):
        """_load_model should call transformers.pipeline."""
        import torch

        manager = HFTransformersModelManager(
            device="cpu",
            torch_dtype=torch.float32,
            ttl_seconds=60,
            max_loaded=1,
        )

        mock_pipeline_fn = MagicMock(return_value=MagicMock())
        mock_transformers = MagicMock()
        mock_transformers.pipeline = mock_pipeline_fn

        # Mock the `from transformers import pipeline` that _load_model does
        with patch.dict(sys.modules, {"transformers": mock_transformers}):
            manager._load_model("facebook/wav2vec2-large-960h")

            mock_pipeline_fn.assert_called_once_with(
                "automatic-speech-recognition",
                model="facebook/wav2vec2-large-960h",
                device="cpu",
                torch_dtype=torch.float32,
            )

        manager.shutdown()

    @pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
    def test_get_local_cache_stats_without_storage(self):
        """get_local_cache_stats returns None without S3 storage."""
        import torch

        manager = HFTransformersModelManager(
            device="cpu",
            torch_dtype=torch.float32,
            ttl_seconds=60,
            max_loaded=1,
        )

        assert manager.get_local_cache_stats() is None
        manager.shutdown()

    @pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
    def test_get_local_cache_stats_with_storage(self):
        """get_local_cache_stats returns stats from S3 storage."""
        import torch

        mock_storage = MagicMock()
        mock_storage.get_cache_stats.return_value = {
            "models": ["openai/whisper-large-v3"],
            "total_size_mb": 3072.5,
            "model_count": 1,
        }

        manager = HFTransformersModelManager(
            device="cpu",
            torch_dtype=torch.float32,
            model_storage=mock_storage,
            ttl_seconds=60,
            max_loaded=1,
        )

        stats = manager.get_local_cache_stats()
        assert stats is not None
        assert stats["model_count"] == 1
        mock_storage.get_cache_stats.assert_called_once()
        manager.shutdown()

    @pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
    def test_load_model_with_s3_storage(self):
        """_load_model should download from S3 when storage is configured."""
        from pathlib import Path

        import torch

        mock_storage = MagicMock()
        mock_storage.ensure_local.return_value = Path(
            "/models/s3-cache/openai--whisper-large-v3"
        )

        manager = HFTransformersModelManager(
            device="cpu",
            torch_dtype=torch.float32,
            model_storage=mock_storage,
            ttl_seconds=60,
            max_loaded=1,
        )

        mock_pipeline_fn = MagicMock(return_value=MagicMock())
        mock_transformers = MagicMock()
        mock_transformers.pipeline = mock_pipeline_fn

        with patch.dict(sys.modules, {"transformers": mock_transformers}):
            manager._load_model("openai/whisper-large-v3")

            # Should call ensure_local to download from S3
            mock_storage.ensure_local.assert_called_once_with("openai/whisper-large-v3")

            # Should pass local path to pipeline, not model ID
            mock_pipeline_fn.assert_called_once_with(
                "automatic-speech-recognition",
                model="/models/s3-cache/openai--whisper-large-v3",
                device="cpu",
                torch_dtype=torch.float32,
            )

        manager.shutdown()


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestHFASREngineS3Storage:
    """Test HfAsrBatchEngine S3 storage integration."""

    def test_engine_get_local_cache_stats_without_storage(self):
        """Engine get_local_cache_stats returns None without S3."""
        with patch.dict(os.environ, {"DALSTON_DEVICE": "cpu"}):
            with patch("torch.cuda.is_available", return_value=False):
                HfAsrBatchEngine = load_hf_asr_engine()
                engine = HfAsrBatchEngine()

                assert engine.get_local_cache_stats() is None
                engine.shutdown()

    def test_engine_enables_s3_storage_from_env(self):
        """Engine should enable S3 storage when DALSTON_S3_BUCKET is set."""
        mock_storage_class = MagicMock()
        mock_storage_instance = MagicMock()
        mock_storage_class.from_env.return_value = mock_storage_instance
        mock_storage_instance.get_cache_stats.return_value = {"model_count": 0}

        with patch.dict(
            os.environ,
            {"DALSTON_DEVICE": "cpu", "DALSTON_S3_BUCKET": "test-bucket"},
        ):
            with patch("torch.cuda.is_available", return_value=False):
                with patch(
                    "dalston.engine_sdk.model_storage.S3ModelStorage",
                    mock_storage_class,
                ):
                    HfAsrBatchEngine = load_hf_asr_engine()
                    engine = HfAsrBatchEngine()

                    # S3ModelStorage should have been initialized
                    mock_storage_class.from_env.assert_called_once()

                    # get_local_cache_stats should return storage stats
                    stats = engine.get_local_cache_stats()
                    assert stats is not None

                    engine.shutdown()
