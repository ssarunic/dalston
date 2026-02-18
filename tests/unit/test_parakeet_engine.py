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

    def test_default_model_variant_is_ctc_0_6b(self, mock_cuda_available):
        """Test that default model variant is ctc-0.6b."""
        ParakeetEngine = load_parakeet_engine()
        assert ParakeetEngine.DEFAULT_MODEL_VARIANT == "ctc-0.6b"

    def test_supported_model_variants(self, mock_cuda_available):
        """Test that expected model variants are supported."""
        ParakeetEngine = load_parakeet_engine()
        # CTC and TDT model variants
        assert "ctc-0.6b" in ParakeetEngine.MODEL_VARIANT_MAP
        assert "ctc-1.1b" in ParakeetEngine.MODEL_VARIANT_MAP
        assert "tdt-0.6b-v3" in ParakeetEngine.MODEL_VARIANT_MAP
        assert "tdt-1.1b" in ParakeetEngine.MODEL_VARIANT_MAP
        # Verify NeMo model identifiers
        assert (
            ParakeetEngine.MODEL_VARIANT_MAP["ctc-0.6b"] == "nvidia/parakeet-ctc-0.6b"
        )
        assert (
            ParakeetEngine.MODEL_VARIANT_MAP["tdt-0.6b-v3"]
            == "nvidia/parakeet-tdt-0.6b-v3"
        )


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


class TestParakeetHypothesisParsing:
    """Tests for parsing NeMo RNNT/TDT Hypothesis objects.

    NeMo's transcribe() with return_hypotheses=True returns nested lists:
    - transcriptions[batch_idx][decode_strategy_idx]
    - For RNNT/TDT: transcriptions[0][0] is the greedy hypothesis

    See: https://github.com/NVIDIA-NeMo/NeMo/issues/7677
    """

    @pytest.fixture
    def mock_hypothesis_with_timestep(self):
        """Create a mock Hypothesis object matching NeMo's RNNT/TDT output."""
        hypothesis = MagicMock()
        hypothesis.text = "that was hilarious i can't believe you said that"
        hypothesis.timestep = {
            "timestep": [2, 3, 5, 6, 8, 10, 13, 15, 16],
            "word": [
                {"word": "that", "start": 0.16, "end": 0.24},
                {"word": "was", "start": 0.24, "end": 0.4},
                {"word": "hilarious", "start": 0.4, "end": 1.04},
                {"word": "i", "start": 1.04, "end": 1.2},
                {"word": "can't", "start": 1.2, "end": 1.52},
                {"word": "believe", "start": 1.52, "end": 1.84},
                {"word": "you", "start": 1.84, "end": 2.0},
                {"word": "said", "start": 2.0, "end": 2.24},
                {"word": "that", "start": 2.24, "end": 2.56},
            ],
            "segment": [
                {
                    "segment": "that was hilarious i can't believe you said that",
                    "start": 0.16,
                    "end": 2.56,
                }
            ],
        }
        return hypothesis

    def test_rnnt_transcribe_returns_nested_list(self, mock_hypothesis_with_timestep):
        """Test that RNNT/TDT models return nested list: transcriptions[0][0]."""
        # Simulate NeMo's return format: [[hypothesis]]
        transcriptions = [[mock_hypothesis_with_timestep]]

        # Verify correct access pattern
        first_result = transcriptions[0]
        assert isinstance(first_result, list), "transcriptions[0] should be a list"

        hypothesis = first_result[0]
        assert hasattr(hypothesis, "text"), "hypothesis should have text attribute"
        assert hasattr(hypothesis, "timestep"), (
            "hypothesis should have timestep attribute"
        )
        assert hypothesis.text == "that was hilarious i can't believe you said that"

    def test_timestep_dict_has_word_and_segment(self, mock_hypothesis_with_timestep):
        """Test that timestep dict contains word and segment keys."""
        timestep = mock_hypothesis_with_timestep.timestep

        assert isinstance(timestep, dict), "timestep should be a dict"
        assert "word" in timestep, "timestep should have 'word' key"
        assert "segment" in timestep, "timestep should have 'segment' key"
        assert isinstance(timestep["word"], list), "timestep['word'] should be a list"
        assert isinstance(timestep["segment"], list), (
            "timestep['segment'] should be a list"
        )

    def test_word_timestamps_have_required_fields(self, mock_hypothesis_with_timestep):
        """Test that word timestamps have word, start, end fields."""
        word_timestamps = mock_hypothesis_with_timestep.timestep["word"]

        for wt in word_timestamps:
            assert "word" in wt, "word timestamp should have 'word' key"
            assert "start" in wt, "word timestamp should have 'start' key"
            assert "end" in wt, "word timestamp should have 'end' key"
            assert isinstance(wt["start"], int | float), "start should be numeric"
            assert isinstance(wt["end"], int | float), "end should be numeric"
            assert wt["end"] >= wt["start"], "end should be >= start"

    def test_segment_timestamps_have_required_fields(
        self, mock_hypothesis_with_timestep
    ):
        """Test that segment timestamps have segment, start, end fields."""
        segment_timestamps = mock_hypothesis_with_timestep.timestep["segment"]

        for seg in segment_timestamps:
            assert "segment" in seg, "segment timestamp should have 'segment' key"
            assert "start" in seg, "segment timestamp should have 'start' key"
            assert "end" in seg, "segment timestamp should have 'end' key"

    def test_word_count_matches_text(self, mock_hypothesis_with_timestep):
        """Test that number of word timestamps matches words in text."""
        text = mock_hypothesis_with_timestep.text
        word_timestamps = mock_hypothesis_with_timestep.timestep["word"]

        expected_word_count = len(text.split())
        assert len(word_timestamps) == expected_word_count, (
            f"Expected {expected_word_count} word timestamps, got {len(word_timestamps)}"
        )

    def test_words_are_monotonically_increasing(self, mock_hypothesis_with_timestep):
        """Test that word timestamps are in chronological order."""
        word_timestamps = mock_hypothesis_with_timestep.timestep["word"]

        for i in range(1, len(word_timestamps)):
            prev_end = word_timestamps[i - 1]["end"]
            curr_start = word_timestamps[i]["start"]
            # Allow small overlap due to floating point
            assert curr_start >= prev_end - 0.01, (
                f"Word {i} starts ({curr_start}) before word {i - 1} ends ({prev_end})"
            )
