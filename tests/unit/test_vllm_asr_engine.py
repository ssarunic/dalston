"""Unit tests for vLLM-ASR transcription engine.

Tests engine logic and adapters without loading actual model weights or vLLM.
"""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HAS_TORCH = importlib.util.find_spec("torch") is not None


# ---------------------------------------------------------------------------
# Adapter tests (no GPU or vLLM required)
# ---------------------------------------------------------------------------


def load_adapter_module(name: str):
    """Load an adapter module from the engines directory using importlib."""
    adapter_path = Path(f"engines/stt-transcribe/vllm-asr/adapters/{name}.py")
    if not adapter_path.exists():
        pytest.skip(f"Adapter {name} not found at {adapter_path}")

    spec = importlib.util.spec_from_file_location(
        f"vllm_asr_adapter_{name}", adapter_path
    )
    if spec is None or spec.loader is None:
        pytest.skip(f"Could not load adapter spec for {name}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[f"vllm_asr_adapter_{name}"] = module

    # Ensure base adapter is loadable
    base_path = Path("engines/stt-transcribe/vllm-asr/adapters/base.py")
    base_spec = importlib.util.spec_from_file_location(
        "vllm_asr_adapter_base", base_path
    )
    if base_spec and base_spec.loader:
        base_module = importlib.util.module_from_spec(base_spec)
        sys.modules["vllm_asr_adapter_base"] = base_module

    spec.loader.exec_module(module)
    return module


class TestVoxtralAdapter:
    """Test Voxtral adapter prompt building and output parsing."""

    @pytest.fixture
    def adapter(self):
        """Import and create VoxtralAdapter."""
        # Import directly from the engine directory
        sys.path.insert(0, str(Path("engines/stt-transcribe/vllm-asr").absolute()))
        try:
            from adapters.voxtral import VoxtralAdapter

            return VoxtralAdapter()
        finally:
            sys.path.pop(0)

    def test_build_messages_default_language(self, adapter, tmp_path):
        """Messages should use generic prompt when no language specified."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language=None)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert len(messages[0]["content"]) == 2

        # First content should be audio
        assert messages[0]["content"][0]["type"] == "audio_url"
        assert str(audio_file) in messages[0]["content"][0]["audio_url"]["url"]

        # Second content should be text prompt
        assert messages[0]["content"][1]["type"] == "text"
        assert "Transcribe" in messages[0]["content"][1]["text"]

    def test_build_messages_english(self, adapter, tmp_path):
        """Messages should use English-specific prompt."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language="en")

        text_content = messages[0]["content"][1]["text"]
        assert "English" in text_content

    def test_build_messages_spanish(self, adapter, tmp_path):
        """Messages should use Spanish-specific prompt."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language="es")

        text_content = messages[0]["content"][1]["text"]
        assert "Spanish" in text_content

    def test_parse_output_basic(self, adapter):
        """Output parsing should produce valid TranscribeOutput."""
        result = adapter.parse_output("Hello world, this is a test.", language="en")

        assert result.text == "Hello world, this is a test."
        assert result.language == "en"
        assert result.runtime == "vllm-asr"
        assert result.skipped is False
        assert len(result.segments) == 1
        assert result.segments[0].text == "Hello world, this is a test."

    def test_parse_output_strips_whitespace(self, adapter):
        """Output parsing should strip leading/trailing whitespace."""
        result = adapter.parse_output("  Hello world  \n\n", language="en")

        assert result.text == "Hello world"
        assert result.segments[0].text == "Hello world"

    def test_parse_output_no_word_timestamps(self, adapter):
        """Output should have segment timestamps only, no words."""
        result = adapter.parse_output("Test", language="en")

        assert result.segments[0].words is None

    def test_parse_output_auto_language(self, adapter):
        """Auto language should default to 'en'."""
        result = adapter.parse_output("Test", language=None)

        assert result.language == "en"

    def test_sampling_kwargs(self, adapter):
        """Sampling kwargs should use deterministic temperature."""
        kwargs = adapter.get_sampling_kwargs()

        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 4096

    def test_supported_languages(self, adapter):
        """Voxtral should support 8 languages."""
        sys.path.insert(0, str(Path("engines/stt-transcribe/vllm-asr").absolute()))
        try:
            from adapters.voxtral import SUPPORTED_LANGUAGES

            assert len(SUPPORTED_LANGUAGES) == 8
            assert "en" in SUPPORTED_LANGUAGES
            assert "es" in SUPPORTED_LANGUAGES
            assert "fr" in SUPPORTED_LANGUAGES
        finally:
            sys.path.pop(0)


class TestQwen2AudioAdapter:
    """Test Qwen2-Audio adapter prompt building and output parsing."""

    @pytest.fixture
    def adapter(self):
        """Import and create Qwen2AudioAdapter."""
        sys.path.insert(0, str(Path("engines/stt-transcribe/vllm-asr").absolute()))
        try:
            from adapters.qwen2_audio import Qwen2AudioAdapter

            return Qwen2AudioAdapter()
        finally:
            sys.path.pop(0)

    def test_build_messages_default_language(self, adapter, tmp_path):
        """Messages should use generic prompt when no language specified."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language=None)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert len(messages[0]["content"]) == 2
        assert messages[0]["content"][0]["type"] == "audio_url"

    def test_build_messages_with_language(self, adapter, tmp_path):
        """Messages should include language instruction."""
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        messages = adapter.build_messages(audio_path=audio_file, language="zh")

        text_content = messages[0]["content"][1]["text"]
        assert "zh" in text_content

    def test_parse_output_basic(self, adapter):
        """Output parsing should produce valid TranscribeOutput."""
        result = adapter.parse_output("Hello world", language="en")

        assert result.text == "Hello world"
        assert result.language == "en"
        assert result.runtime == "vllm-asr"
        assert len(result.segments) == 1

    def test_sampling_kwargs(self, adapter):
        """Sampling kwargs should use deterministic temperature."""
        kwargs = adapter.get_sampling_kwargs()

        assert kwargs["temperature"] == 0.0


class TestAdapterRegistry:
    """Test adapter registry and get_adapter function."""

    @pytest.fixture(autouse=True)
    def setup_path(self):
        """Add engine directory to path."""
        engine_dir = str(Path("engines/stt-transcribe/vllm-asr").absolute())
        sys.path.insert(0, engine_dir)
        yield
        sys.path.remove(engine_dir)

    def test_get_adapter_voxtral_mini(self):
        """Registry should return VoxtralAdapter for Voxtral Mini."""
        from adapters import get_adapter
        from adapters.voxtral import VoxtralAdapter

        adapter = get_adapter("mistralai/Voxtral-Mini-3B-2507")
        assert isinstance(adapter, VoxtralAdapter)

    def test_get_adapter_voxtral_small(self):
        """Registry should return VoxtralAdapter for Voxtral Small."""
        from adapters import get_adapter
        from adapters.voxtral import VoxtralAdapter

        adapter = get_adapter("mistralai/Voxtral-Small-24B-2507")
        assert isinstance(adapter, VoxtralAdapter)

    def test_get_adapter_qwen2_audio(self):
        """Registry should return Qwen2AudioAdapter for Qwen2-Audio."""
        from adapters import get_adapter
        from adapters.qwen2_audio import Qwen2AudioAdapter

        adapter = get_adapter("Qwen/Qwen2-Audio-7B-Instruct")
        assert isinstance(adapter, Qwen2AudioAdapter)

    def test_get_adapter_unknown_model_raises(self):
        """Unknown model should raise ValueError."""
        from adapters import get_adapter

        with pytest.raises(ValueError, match="No adapter for model"):
            get_adapter("unknown/model-id")

    def test_registry_contains_expected_models(self):
        """Registry should contain all supported models."""
        from adapters import ADAPTER_REGISTRY

        assert "mistralai/Voxtral-Mini-3B-2507" in ADAPTER_REGISTRY
        assert "mistralai/Voxtral-Small-24B-2507" in ADAPTER_REGISTRY
        assert "Qwen/Qwen2-Audio-7B-Instruct" in ADAPTER_REGISTRY
        assert len(ADAPTER_REGISTRY) == 3


# ---------------------------------------------------------------------------
# Engine tests (require torch for mock CUDA)
# ---------------------------------------------------------------------------


def load_vllm_asr_engine():
    """Load VLLMASREngine from engines directory using importlib."""
    engine_path = Path("engines/stt-transcribe/vllm-asr/engine.py")
    if not engine_path.exists():
        pytest.skip("vLLM-ASR engine not found")

    spec = importlib.util.spec_from_file_location("vllm_asr_engine", engine_path)
    if spec is None or spec.loader is None:
        pytest.skip("Could not load vLLM-ASR engine spec")

    module = importlib.util.module_from_spec(spec)
    sys.modules["vllm_asr_engine"] = module
    spec.loader.exec_module(module)
    return module.VLLMASREngine


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestVLLMASREngine:
    """Test VLLMASREngine without loading actual model."""

    @pytest.fixture
    def mock_cuda(self):
        """Mock CUDA availability."""
        with patch.dict(
            os.environ,
            {
                "DALSTON_ENGINE_ID": "vllm-asr",
                "DALSTON_DEFAULT_MODEL_ID": "mistralai/Voxtral-Mini-3B-2507",
            },
        ):
            with patch("torch.cuda.is_available", return_value=True):
                with patch("torch.cuda.device_count", return_value=1):
                    yield

    @pytest.fixture
    def engine(self, mock_cuda):
        """Create engine instance with mocked CUDA."""
        # Clear cached modules to ensure fresh load
        for key in list(sys.modules.keys()):
            if "vllm_asr" in key:
                del sys.modules[key]

        VLLMASREngine = load_vllm_asr_engine()
        engine = VLLMASREngine()
        return engine

    def test_engine_init(self, engine):
        """Engine should initialize with correct defaults."""
        assert engine._engine_id == "vllm-asr"
        assert engine._default_model_id == "mistralai/Voxtral-Mini-3B-2507"
        assert engine._llm is None
        assert engine._loaded_model_id is None

    def test_engine_init_no_cuda_raises(self):
        """Engine should raise RuntimeError when CUDA is unavailable."""
        with patch.dict(
            os.environ,
            {"DALSTON_ENGINE_ID": "vllm-asr"},
        ):
            with patch("torch.cuda.is_available", return_value=False):
                VLLMASREngine = load_vllm_asr_engine()

                with pytest.raises(RuntimeError, match="requires CUDA"):
                    VLLMASREngine()

    def test_get_capabilities(self, engine):
        """Engine capabilities should be correct."""
        caps = engine.get_capabilities()

        assert caps.runtime == "vllm-asr"
        assert caps.version == "1.0.0"
        assert caps.stages == ["transcribe"]
        assert caps.languages is None  # Multilingual
        assert caps.supports_word_timestamps is False
        assert caps.supports_streaming is False
        assert caps.gpu_required is True
        assert caps.supports_cpu is False
        assert caps.runtime == "vllm-asr"

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
        assert health["runtime"] == "vllm-asr"
        assert health["model_loaded"] is False
        assert health["loaded_model_id"] is None
        assert "mistralai/Voxtral-Mini-3B-2507" in health["supported_models"]

    def test_ensure_model_loaded_unknown_adapter_raises(self, engine):
        """Loading a model without an adapter should raise ValueError."""
        with pytest.raises(ValueError, match="No adapter for model"):
            engine._ensure_model_loaded("unknown/model-id")

    def test_ensure_model_loaded_vllm_not_installed(self, engine):
        """Missing vLLM should raise RuntimeError with install instructions."""
        with patch.dict(sys.modules, {"vllm": None}):
            with patch(
                "builtins.__import__", side_effect=ImportError("No module named 'vllm'")
            ):
                with pytest.raises((RuntimeError, ImportError)):
                    engine._ensure_model_loaded("mistralai/Voxtral-Mini-3B-2507")

    def test_process_with_mocked_vllm(self, engine, tmp_path):
        """Process should return valid TranscribeOutput with mocked vLLM."""
        # Create a test audio file
        audio_file = tmp_path / "test.wav"
        audio_file.touch()

        # Mock vLLM imports and model
        mock_output = MagicMock()
        mock_output.outputs = [MagicMock(text="Hello, this is a test transcription.")]

        mock_llm = MagicMock()
        mock_llm.chat.return_value = [mock_output]

        # Set up the mocked model
        engine._llm = mock_llm
        engine._loaded_model_id = "mistralai/Voxtral-Mini-3B-2507"

        # Create mock TaskInput
        mock_input = MagicMock()
        mock_input.audio_path = audio_file
        mock_input.config = {
            "runtime_model_id": "mistralai/Voxtral-Mini-3B-2507",
            "language": "en",
        }

        # Mock SamplingParams import
        mock_sampling_params = MagicMock()
        with patch.dict(
            sys.modules, {"vllm": MagicMock(SamplingParams=mock_sampling_params)}
        ):
            result = engine.process(mock_input)

        assert result.data.text == "Hello, this is a test transcription."
        assert result.data.runtime == "vllm-asr"
        assert result.data.language == "en"
        assert len(result.data.segments) == 1
        assert result.data.skipped is False

    def test_process_vocabulary_warning(self, engine, tmp_path):
        """Process should add warning when vocabulary is provided."""
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
            "runtime_model_id": "mistralai/Voxtral-Mini-3B-2507",
            "language": "en",
            "vocabulary": ["Dalston", "vLLM"],
        }

        mock_sampling_params = MagicMock()
        with patch.dict(
            sys.modules, {"vllm": MagicMock(SamplingParams=mock_sampling_params)}
        ):
            result = engine.process(mock_input)

        assert len(result.data.warnings) > 0
        assert "not supported" in result.data.warnings[0].lower()

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

    def test_custom_engine_id(self):
        """Custom ENGINE_ID should be respected."""
        with patch.dict(
            os.environ,
            {
                "DALSTON_ENGINE_ID": "custom-vllm",
                "DALSTON_DEFAULT_MODEL_ID": "mistralai/Voxtral-Mini-3B-2507",
            },
        ):
            with patch("torch.cuda.is_available", return_value=True):
                with patch("torch.cuda.device_count", return_value=1):
                    VLLMASREngine = load_vllm_asr_engine()
                    engine = VLLMASREngine()

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
            with patch("torch.cuda.is_available", return_value=True):
                with patch("torch.cuda.device_count", return_value=1):
                    VLLMASREngine = load_vllm_asr_engine()
                    engine = VLLMASREngine()

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
            with patch("torch.cuda.is_available", return_value=True):
                with patch("torch.cuda.device_count", return_value=1):
                    VLLMASREngine = load_vllm_asr_engine()
                    engine = VLLMASREngine()

                    assert engine._max_model_len == 8192
