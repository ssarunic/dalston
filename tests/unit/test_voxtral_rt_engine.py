"""Contract tests for Voxtral realtime engine on vLLM."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.common.pipeline_types import TranscribeInput

HAS_TORCH = importlib.util.find_spec("torch") is not None


@pytest.fixture(autouse=True)
def _cleanup_injected_modules():
    """Remove only this test's dynamically loaded module after each test."""
    yield
    sys.modules.pop("voxtral_rt_engine_test", None)


def _load_engine_class():
    """Load VoxtralRealtimeEngine directly from engine file."""
    sys.modules.pop("voxtral_rt_engine_test", None)
    engine_path = (
        Path(__file__).resolve().parents[2] / "engines/stt-rt/voxtral/engine.py"
    )
    if not engine_path.exists():
        pytest.skip(f"Voxtral realtime engine not found at {engine_path}")
    spec = importlib.util.spec_from_file_location("voxtral_rt_engine_test", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["voxtral_rt_engine_test"] = module
    spec.loader.exec_module(module)
    return module.VoxtralRealtimeEngine


def _mock_vllm(chat_text: str = "hello world"):
    """Build a mock vLLM module and return module + mock llm instance."""
    mock_llm = MagicMock()
    mock_output = SimpleNamespace(outputs=[SimpleNamespace(text=chat_text)])
    mock_llm.chat.return_value = [mock_output]

    mock_llm_cls = MagicMock(return_value=mock_llm)
    mock_sampling_params = MagicMock(return_value=SimpleNamespace())
    vllm_module = SimpleNamespace(LLM=mock_llm_cls, SamplingParams=mock_sampling_params)
    return vllm_module, mock_llm, mock_llm_cls


@pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")
class TestVoxtralRtVllmEngine:
    """Verify realtime contract and vLLM-backed behavior."""

    def test_init_requires_cuda(self):
        with patch("torch.cuda.is_available", return_value=False):
            VoxtralRealtimeEngine = _load_engine_class()
            with pytest.raises(RuntimeError, match="requires CUDA"):
                VoxtralRealtimeEngine()

    def test_load_models_initializes_vllm(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.device_count", return_value=1),
        ):
            VoxtralRealtimeEngine = _load_engine_class()
            engine = VoxtralRealtimeEngine()

            vllm_module, _, mock_llm_cls = _mock_vllm()
            with patch.dict(sys.modules, {"vllm": vllm_module}):
                engine.load_models()

            assert engine._llm is not None
            assert engine._loaded_model_id == "mistralai/Voxtral-Mini-4B-Realtime-2602"
            assert mock_llm_cls.called

    def test_transcribe_segment_fallback_when_no_timestamp_tokens(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.device_count", return_value=1),
        ):
            VoxtralRealtimeEngine = _load_engine_class()
            engine = VoxtralRealtimeEngine()

            vllm_module, _, _ = _mock_vllm(chat_text="hello world")
            with patch.dict(sys.modules, {"vllm": vllm_module}):
                engine.load_models()

                audio = np.zeros(320, dtype=np.float32)
                result = engine.transcribe(
                    audio,
                    TranscribeInput(language="en", word_timestamps=True),
                )

            assert result.text == "hello world"
            assert result.engine_id == "vllm-asr"
            assert result.timestamp_granularity.value == "segment"
            assert result.segments[0].start == 0.0
            assert result.segments[0].end == 0.02
            assert any("Word timestamps requested" in w for w in result.warnings)

    def test_transcribe_parses_timestamp_tokens_into_words(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.device_count", return_value=1),
        ):
            VoxtralRealtimeEngine = _load_engine_class()
            engine = VoxtralRealtimeEngine()

            text = "<|0.00|>hello<|0.08|><|0.08|>world<|0.16|>"
            vllm_module, _, _ = _mock_vllm(chat_text=text)
            with patch.dict(sys.modules, {"vllm": vllm_module}):
                engine.load_models()

                audio = np.zeros(320, dtype=np.float32)
                result = engine.transcribe(
                    audio,
                    TranscribeInput(language="en", word_timestamps=True),
                )

            assert result.text == "hello world"
            assert result.timestamp_granularity.value == "word"
            words = result.segments[0].words or []
            assert len(words) == 2
            assert words[0].text == "hello"
            assert words[0].start == 0.0
            assert words[0].end == 0.08
            assert words[1].text == "world"
            assert words[1].start == 0.08
            assert words[1].end == 0.16

    def test_engine_id_and_models_metadata(self):
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.device_count", return_value=1),
        ):
            VoxtralRealtimeEngine = _load_engine_class()
            engine = VoxtralRealtimeEngine()

        assert engine.get_engine_id() == "vllm-asr"
        assert engine.get_models() == ["voxtral-mini-4b"]
        assert "en" in engine.get_languages()
