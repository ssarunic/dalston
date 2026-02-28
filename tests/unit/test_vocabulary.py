"""Unit tests for vocabulary boosting support.

Tests vocabulary field validation at API level and passthrough to engines.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# =============================================================================
# Pipeline Types Tests
# =============================================================================


class TestTranscribeInputVocabulary:
    """Tests for vocabulary field in TranscribeInput."""

    def test_vocabulary_field_accepts_list_of_strings(self):
        """Test that vocabulary accepts a list of strings."""
        from dalston.common.pipeline_types import TranscribeInput

        input_data = TranscribeInput(vocabulary=["Dalston", "FastAPI", "Redis"])
        assert input_data.vocabulary == ["Dalston", "FastAPI", "Redis"]

    def test_vocabulary_field_defaults_to_none(self):
        """Test that vocabulary defaults to None."""
        from dalston.common.pipeline_types import TranscribeInput

        input_data = TranscribeInput()
        assert input_data.vocabulary is None

    def test_vocabulary_field_allows_empty_list(self):
        """Test that vocabulary allows empty list."""
        from dalston.common.pipeline_types import TranscribeInput

        input_data = TranscribeInput(vocabulary=[])
        assert input_data.vocabulary == []


# =============================================================================
# Request Model Tests
# =============================================================================


class TestTranscriptionCreateParamsVocabulary:
    """Tests for vocabulary field in request models."""

    def test_vocabulary_included_in_job_parameters(self):
        """Test that vocabulary is included in job parameters."""
        from dalston.gateway.models.requests import TranscriptionCreateParams

        params = TranscriptionCreateParams(
            vocabulary=["term1", "term2", "term3"],
        )
        job_params = params.to_job_parameters()

        assert "vocabulary" in job_params
        assert job_params["vocabulary"] == ["term1", "term2", "term3"]

    def test_vocabulary_omitted_when_none(self):
        """Test that vocabulary is not in job parameters when None."""
        from dalston.gateway.models.requests import TranscriptionCreateParams

        params = TranscriptionCreateParams()
        job_params = params.to_job_parameters()

        assert "vocabulary" not in job_params


# =============================================================================
# Faster-Whisper Engine Tests
# =============================================================================

# Check if faster_whisper is available
try:
    import faster_whisper  # noqa: F401

    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False


def load_whisper_engine():
    """Load WhisperEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/faster-whisper/engine.py")
    if not engine_path.exists():
        pytest.skip("Faster-whisper engine not found")

    spec = importlib.util.spec_from_file_location("whisper_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load whisper engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["whisper_engine"] = module
    spec.loader.exec_module(module)
    return module.WhisperEngine


@pytest.mark.skipif(not HAS_FASTER_WHISPER, reason="faster_whisper not installed")
class TestWhisperEngineVocabulary:
    """Tests for vocabulary support in faster-whisper engine."""

    @pytest.fixture
    def mock_whisper_model(self):
        """Mock WhisperModel."""
        mock_model = MagicMock()
        # Return a generator for segments and info object
        mock_segment = MagicMock()
        mock_segment.start = 0.0
        mock_segment.end = 1.0
        mock_segment.text = "Hello world"
        mock_segment.words = []

        mock_info = MagicMock()
        mock_info.language = "en"
        mock_info.language_probability = 0.99
        mock_info.duration = 1.0

        mock_model.transcribe.return_value = (iter([mock_segment]), mock_info)
        return mock_model

    @pytest.fixture
    def engine_with_mock_model(self, mock_whisper_model, monkeypatch):
        """Create WhisperEngine with mocked model."""
        # Force CPU mode to avoid CUDA requirement for large-v3
        monkeypatch.setenv("DALSTON_DEVICE", "cpu")
        with patch("faster_whisper.WhisperModel", return_value=mock_whisper_model):
            WhisperEngine = load_whisper_engine()
            engine = WhisperEngine()
            engine._model = mock_whisper_model
            yield engine, mock_whisper_model

    def test_vocabulary_passed_as_hotwords(self, engine_with_mock_model):
        """Test that vocabulary is passed to transcribe as hotwords."""
        from dalston.engine_sdk import TaskInput

        engine, mock_model = engine_with_mock_model

        input_data = TaskInput(
            task_id=str(uuid4()),
            job_id=str(uuid4()),
            audio_path=Path("/tmp/test.wav"),
            config={"vocabulary": ["Dalston", "FastAPI", "Redis"]},
        )

        engine.process(input_data)

        # Verify transcribe was called with hotwords
        call_kwargs = mock_model.transcribe.call_args.kwargs
        assert "hotwords" in call_kwargs
        assert call_kwargs["hotwords"] == "Dalston FastAPI Redis"

    def test_vocabulary_not_passed_when_none(self, engine_with_mock_model):
        """Test that hotwords is not passed when vocabulary is None."""
        from dalston.engine_sdk import TaskInput

        engine, mock_model = engine_with_mock_model

        input_data = TaskInput(
            task_id=str(uuid4()),
            job_id=str(uuid4()),
            audio_path=Path("/tmp/test.wav"),
            config={},
        )

        engine.process(input_data)

        # Verify hotwords was not passed
        call_kwargs = mock_model.transcribe.call_args.kwargs
        assert "hotwords" not in call_kwargs

    def test_vocabulary_empty_list_not_passed(self, engine_with_mock_model):
        """Test that hotwords is not passed for empty vocabulary."""
        from dalston.engine_sdk import TaskInput

        engine, mock_model = engine_with_mock_model

        input_data = TaskInput(
            task_id=str(uuid4()),
            job_id=str(uuid4()),
            audio_path=Path("/tmp/test.wav"),
            config={"vocabulary": []},
        )

        engine.process(input_data)

        # Verify hotwords was not passed (empty list is falsy)
        call_kwargs = mock_model.transcribe.call_args.kwargs
        assert "hotwords" not in call_kwargs


# =============================================================================
# Parakeet Engine Tests
# =============================================================================


def load_parakeet_engine():
    """Load ParakeetEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/parakeet/engine.py")
    if not engine_path.exists():
        pytest.skip("Parakeet engine not found")

    spec = importlib.util.spec_from_file_location("parakeet_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load parakeet engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["parakeet_engine"] = module
    spec.loader.exec_module(module)
    return module.ParakeetEngine


# Skip all parakeet tests if torch not installed
torch = pytest.importorskip("torch")


@pytest.mark.skipif(
    not torch.cuda.is_available(),
    reason="CUDA required for Parakeet engine tests",
)
class TestParakeetEngineVocabulary:
    """Tests for vocabulary handling in Parakeet engine."""

    @pytest.fixture
    def mock_cuda_available(self):
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
    def mock_nemo_model(self):
        """Mock NeMo ASR model."""
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.eval.return_value = mock_model

        # Create mock hypothesis
        mock_hypothesis = MagicMock()
        mock_hypothesis.text = "Hello world"
        mock_hypothesis.timestep = None

        mock_model.transcribe.return_value = [[mock_hypothesis]]
        return mock_model

    @pytest.fixture
    def engine_with_mock_model(self, mock_cuda_available, mock_nemo_model):
        """Create ParakeetEngine with mocked NeMo model."""
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
                return_value=mock_nemo_model,
            ):
                ParakeetEngine = load_parakeet_engine()
                engine = ParakeetEngine()
                engine._model = mock_nemo_model
                engine._model_name = "nvidia/parakeet-ctc-0.6b"
                yield engine

    def test_vocabulary_boosting_enabled_no_warning(self, engine_with_mock_model):
        """Test that vocabulary boosting is enabled without warning when successful."""
        from dalston.engine_sdk import TaskInput

        engine = engine_with_mock_model

        input_data = TaskInput(
            task_id=str(uuid4()),
            job_id=str(uuid4()),
            audio_path=Path("/tmp/test.wav"),
            config={"vocabulary": ["term1", "term2", "term3"]},
        )

        output = engine.process(input_data)

        # No warnings when vocabulary boosting succeeds
        # (Parakeet now supports vocabulary via GPU-PB)
        assert output.data.warnings == []

    def test_vocabulary_none_no_warning(self, engine_with_mock_model):
        """Test that no vocabulary produces no warning."""
        from dalston.engine_sdk import TaskInput

        engine = engine_with_mock_model

        input_data = TaskInput(
            task_id=str(uuid4()),
            job_id=str(uuid4()),
            audio_path=Path("/tmp/test.wav"),
            config={},
        )

        output = engine.process(input_data)

        # No warnings when vocabulary not provided
        assert output.data.warnings == []
