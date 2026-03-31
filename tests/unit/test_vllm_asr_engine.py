"""Unit tests for vLLM-ASR transcription engine.

Tests engine logic and adapter without loading actual model weights or vLLM.
"""

import importlib.util
import os
import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.common.pipeline_types import TranscriptionRequest
from dalston.engine_sdk.context import BatchTaskContext

HAS_TORCH = importlib.util.find_spec("torch") is not None


def _ctx(input_obj) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="vllm-asr",
        instance="test-instance",
        task_id=getattr(input_obj, "task_id", "test-task"),
        job_id=getattr(input_obj, "job_id", "test-job"),
        stage=getattr(input_obj, "stage", "transcribe"),
    )


# ---------------------------------------------------------------------------
# Adapter tests (no GPU or vLLM required)
# ---------------------------------------------------------------------------


class TestAudioLLMAdapter:
    """Test generic AudioLLMAdapter that works with any vLLM audio model."""

    @pytest.fixture
    def adapter(self):
        from dalston.vllm_asr.adapter import AudioLLMAdapter

        return AudioLLMAdapter()

    def test_build_messages_default_language(self, adapter, tmp_path):
        """Should produce valid messages without language."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language=None)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert len(messages[0]["content"]) == 2
        assert messages[0]["content"][0]["type"] == "audio_url"
        assert str(audio_file) in messages[0]["content"][0]["audio_url"]["url"]
        assert "Transcribe" in messages[0]["content"][1]["text"]

    def test_build_messages_with_language(self, adapter, tmp_path):
        """Should include language code in prompt."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language="ja")

        text_content = messages[0]["content"][1]["text"]
        assert "ja" in text_content

    def test_build_messages_with_vocabulary(self, adapter, tmp_path):
        """Should include vocabulary terms in prompt."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(
            audio_path=audio_file, language="en", vocabulary=["Dalston", "vLLM"]
        )

        text_content = messages[0]["content"][1]["text"]
        assert "Dalston" in text_content
        assert "vLLM" in text_content

    def test_parse_output_basic(self, adapter):
        """Should produce valid Transcript."""
        result = adapter.parse_output(
            "Hello world, this is a test.", language="en", duration=5.0
        )

        assert result.text == "Hello world, this is a test."
        assert result.language == "en"
        assert result.engine_id == "vllm-asr"
        assert len(result.segments) == 1
        assert result.segments[0].text == "Hello world, this is a test."
        assert result.segments[0].end == 5.0

    def test_parse_output_strips_whitespace(self, adapter):
        """Should strip leading/trailing whitespace."""
        result = adapter.parse_output("  Hello world  \n\n", language="en")

        assert result.text == "Hello world"
        assert result.segments[0].text == "Hello world"

    def test_parse_output_no_word_timestamps(self, adapter):
        """Should have segment timestamps only, no words."""
        result = adapter.parse_output("Test", language="en")
        assert result.segments[0].words is None

    def test_parse_output_auto_language_defaults_to_en(self, adapter):
        """Auto/None language should default to en."""
        assert adapter.parse_output("Test", language=None).language == "en"
        assert adapter.parse_output("Test", language="auto").language == "en"

    def test_sampling_kwargs(self, adapter):
        """Default sampling should be deterministic."""
        kwargs = adapter.get_sampling_kwargs()
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 4096


class TestAdapterSingleton:
    """Test module-level adapter singleton."""

    def test_is_audio_llm_adapter(self):
        from dalston.vllm_asr.adapter import AudioLLMAdapter, adapter

        assert isinstance(adapter, AudioLLMAdapter)

    def test_works_end_to_end(self, tmp_path):
        from dalston.vllm_asr.adapter import adapter

        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language="en")
        assert len(messages) == 1

        result = adapter.parse_output("Transcribed text", language="en", duration=3.0)
        assert result.text == "Transcribed text"


# ---------------------------------------------------------------------------
# Audio bridge tests
# ---------------------------------------------------------------------------


class TestVllmAudioBridge:
    """Test shared audio bridge helpers."""

    def test_write_wav_file_pcm16_mono(self, tmp_path):
        """Helper should write mono PCM16 WAV files."""
        from dalston.vllm_asr.audio import write_wav_file

        wav_path = tmp_path / "bridge.wav"
        audio = np.array([0.0, 0.25, -0.25, 0.9], dtype=np.float32)

        write_wav_file(wav_path, audio=audio, sample_rate=16000)

        with wave.open(str(wav_path), "rb") as reader:
            assert reader.getnchannels() == 1
            assert reader.getsampwidth() == 2
            assert reader.getframerate() == 16000
            assert reader.getnframes() == len(audio)

    def test_temporary_wav_file_cleanup(self):
        """Temporary WAV context should remove files on exit."""
        from dalston.vllm_asr.audio import temporary_wav_file

        audio = np.zeros(320, dtype=np.float32)
        with temporary_wav_file(audio=audio, sample_rate=16000) as wav_path:
            assert wav_path.exists()

        assert not wav_path.exists()


# ---------------------------------------------------------------------------
# Engine tests (require torch for mock CUDA)
# ---------------------------------------------------------------------------


def load_vllm_asr_engine():
    """Load VllmAsrBatchEngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/vllm-asr/batch_engine.py")
    if not engine_path.exists():
        pytest.skip("vLLM-ASR engine not found")

    spec = importlib.util.spec_from_file_location("vllm_asr_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load vLLM-ASR engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["vllm_asr_engine"] = module
    spec.loader.exec_module(module)
    return module.VllmAsrBatchEngine


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestVLLMASREngine:
    """Test VllmAsrBatchEngine without loading actual model."""

    @pytest.fixture
    def mock_cuda(self):
        """Mock CUDA availability via pynvml (engine uses pynvml, not torch.cuda, at init)."""
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlDeviceGetCount.return_value = 1
        with patch.dict(
            os.environ,
            {
                "DALSTON_ENGINE_ID": "vllm-asr",
                "DALSTON_DEFAULT_MODEL_ID": "mistralai/Voxtral-Mini-3B-2507",
            },
        ):
            with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
                yield

    @pytest.fixture
    def engine(self, mock_cuda):
        """Create engine instance with mocked CUDA."""
        # Clear cached modules to ensure fresh load
        for key in list(sys.modules.keys()):
            if "vllm_asr" in key:
                del sys.modules[key]

        VllmAsrBatchEngine = load_vllm_asr_engine()
        engine = VllmAsrBatchEngine()
        return engine

    def test_engine_init(self, engine):
        """Engine should initialize with correct defaults."""
        assert engine._engine_id == "vllm-asr"
        assert engine._default_model_id == "mistralai/Voxtral-Mini-3B-2507"
        assert engine._llm is None
        assert engine._loaded_model_id is None

    def test_engine_init_no_cuda_raises(self):
        """Engine should raise RuntimeError when CUDA is unavailable."""
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlInit.side_effect = Exception("No NVIDIA driver")
        with patch.dict(
            os.environ,
            {"DALSTON_ENGINE_ID": "vllm-asr"},
        ):
            with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
                VllmAsrBatchEngine = load_vllm_asr_engine()

                with pytest.raises(RuntimeError, match="requires CUDA"):
                    VllmAsrBatchEngine()

    def test_get_capabilities(self, engine):
        """Engine capabilities should be correct."""
        caps = engine.get_capabilities()

        assert caps.engine_id == "vllm-asr"
        assert caps.version == "1.0.0"
        assert caps.stages == ["transcribe"]
        assert caps.supports_word_timestamps is False
        assert caps.supports_native_streaming is False
        assert caps.gpu_required is True
        assert caps.supports_cpu is False

    def test_health_check_no_model(self, engine):
        """Health check should work without model loaded."""
        with patch("torch.cuda.is_available", return_value=True):
            with patch("torch.cuda.device_count", return_value=1):
                with patch("torch.cuda.memory_allocated", return_value=0):
                    with patch(
                        "torch.cuda.get_device_properties",
                        return_value=MagicMock(total_memory=16e9),
                    ):
                        health = engine.health_check()

        assert health["status"] == "healthy"
        assert health["engine_id"] == "vllm-asr"
        assert health["model_loaded"] is False
        assert health["loaded_model_id"] is None

    def test_ensure_model_loaded_vllm_not_installed(self, engine):
        """Missing vLLM should raise RuntimeError with install instructions."""
        with patch.dict(sys.modules, {"vllm": None}):
            with patch(
                "builtins.__import__", side_effect=ImportError("No module named 'vllm'")
            ):
                with pytest.raises((RuntimeError, ImportError)):
                    engine._ensure_model_loaded("mistralai/Voxtral-Mini-3B-2507")

    def test_process_with_mocked_vllm(self, engine, tmp_path):
        """Process should return valid Transcript with mocked vLLM."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_output = MagicMock()
        mock_output.outputs = [MagicMock(text="Hello, this is a test transcription.")]

        mock_llm = MagicMock()
        mock_llm.chat.return_value = [mock_output]

        engine._llm = mock_llm
        engine._loaded_model_id = "mistralai/Voxtral-Mini-3B-2507"

        mock_input = MagicMock()
        mock_input.audio_path = audio_file
        mock_input.config = {
            "loaded_model_id": "mistralai/Voxtral-Mini-3B-2507",
            "language": "en",
        }
        mock_input.get_transcribe_params.return_value = TranscriptionRequest(
            loaded_model_id="mistralai/Voxtral-Mini-3B-2507",
            language="en",
        )

        mock_sampling_params = MagicMock()
        with patch.dict(
            sys.modules, {"vllm": MagicMock(SamplingParams=mock_sampling_params)}
        ):
            result = engine.process(mock_input, _ctx(mock_input))

        assert result.data.text == "Hello, this is a test transcription."
        assert result.data.engine_id == "vllm-asr"
        assert result.data.language == "en"
        assert len(result.data.segments) == 1

    def test_process_vocabulary_passed_to_llm(self, engine, tmp_path):
        """Process should pass vocabulary terms to the LLM via instruction prompting."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        mock_output = MagicMock()
        mock_output.outputs = [MagicMock(text="Test")]

        mock_llm = MagicMock()
        mock_llm.chat.return_value = [mock_output]

        engine._llm = mock_llm
        engine._loaded_model_id = "mistralai/Voxtral-Mini-3B-2507"

        mock_input = MagicMock()
        mock_input.audio_path = audio_file
        mock_input.config = {
            "loaded_model_id": "mistralai/Voxtral-Mini-3B-2507",
            "language": "en",
            "vocabulary": ["Dalston", "vLLM"],
        }
        mock_input.get_transcribe_params.return_value = TranscriptionRequest(
            loaded_model_id="mistralai/Voxtral-Mini-3B-2507",
            language="en",
            vocabulary=["Dalston", "vLLM"],
        )

        mock_sampling_params = MagicMock()
        with patch.dict(
            sys.modules, {"vllm": MagicMock(SamplingParams=mock_sampling_params)}
        ):
            engine.process(mock_input, _ctx(mock_input))

        chat_call = mock_llm.chat.call_args
        messages = chat_call[1]["messages"]
        message_texts = [
            m["content"]
            if isinstance(m["content"], str)
            else " ".join(
                part.get("text", "")
                for part in m["content"]
                if isinstance(part, dict) and part.get("type") == "text"
            )
            for m in messages
        ]
        combined = " ".join(message_texts)
        assert "Dalston" in combined
        assert "vLLM" in combined

    def test_transcribe_audio_array_with_mocked_vllm(self, engine):
        """Engine should support in-memory audio transcription bridge."""
        mock_output = MagicMock()
        mock_output.outputs = [MagicMock(text="Realtime bridge test")]
        mock_llm = MagicMock()
        mock_llm.chat.return_value = [mock_output]

        engine._llm = mock_llm
        engine._loaded_model_id = "mistralai/Voxtral-Mini-3B-2507"

        audio = np.zeros(320, dtype=np.float32)
        params = TranscriptionRequest(
            loaded_model_id="mistralai/Voxtral-Mini-3B-2507",
            language="en",
        )

        mock_sampling_params = MagicMock()
        with patch.dict(
            sys.modules, {"vllm": MagicMock(SamplingParams=mock_sampling_params)}
        ):
            transcript = engine.transcribe_audio_array(audio, params, sample_rate=16000)

        assert transcript.text == "Realtime bridge test"
        assert transcript.engine_id == "vllm-asr"
        assert transcript.language == "en"

    def test_shutdown_cleans_up(self, engine):
        """Shutdown should clean up model and GPU resources."""
        engine._llm = MagicMock()
        engine._loaded_model_id = "mistralai/Voxtral-Mini-3B-2507"

        with patch("torch.cuda.synchronize"):
            with patch("torch.cuda.empty_cache"):
                engine.shutdown()

        assert engine._llm is None
        assert engine._loaded_model_id is None


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestVLLMASREngineEnvironment:
    """Test engine environment variable handling."""

    @pytest.fixture(autouse=True)
    def _mock_pynvml(self):
        """Mock pynvml for all environment tests."""
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlDeviceGetCount.return_value = 1
        with patch.dict(sys.modules, {"pynvml": mock_pynvml}):
            yield

    def test_custom_engine_id(self):
        """Custom ENGINE_ID should be respected."""
        with patch.dict(
            os.environ,
            {
                "DALSTON_ENGINE_ID": "custom-vllm",
                "DALSTON_DEFAULT_MODEL_ID": "mistralai/Voxtral-Mini-3B-2507",
            },
        ):
            VllmAsrBatchEngine = load_vllm_asr_engine()
            engine = VllmAsrBatchEngine()

            assert engine._engine_id == "custom-vllm"

    def test_custom_gpu_memory_utilization(self):
        """Custom GPU memory utilization should be respected."""
        with patch.dict(
            os.environ,
            {
                "DALSTON_ENGINE_ID": "vllm-asr",
                "DALSTON_VLLM_GPU_MEMORY_UTILIZATION": "0.7",
            },
        ):
            VllmAsrBatchEngine = load_vllm_asr_engine()
            engine = VllmAsrBatchEngine()

            assert engine._gpu_memory_utilization == 0.7

    def test_custom_max_model_len(self):
        """Custom max model length should be respected."""
        with patch.dict(
            os.environ,
            {
                "DALSTON_ENGINE_ID": "vllm-asr",
                "DALSTON_VLLM_MAX_MODEL_LEN": "8192",
            },
        ):
            VllmAsrBatchEngine = load_vllm_asr_engine()
            engine = VllmAsrBatchEngine()

            assert engine._max_model_len == 8192
