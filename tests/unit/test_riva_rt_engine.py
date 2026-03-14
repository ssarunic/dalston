"""Contract tests for Riva NIM real-time transcription engine (unified).

Verifies that the RT engine produces correct Transcript output,
supports streaming (partial results), and handles health checks properly
when communicating with a mocked Riva NIM gRPC sidecar.

These tests mock the Riva client library to avoid GPU/NIM dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.common.pipeline_types import TranscribeInput

UNIFIED_RIVA_DIR = Path("engines/stt-unified/riva")


def _make_mock_word(
    word: str = "hello",
    start_time: float = 0.0,
    end_time: float = 0.5,
    confidence: float = 0.95,
) -> SimpleNamespace:
    return SimpleNamespace(
        word=word,
        start_time=start_time,
        end_time=end_time,
        confidence=confidence,
    )


def _make_mock_alternative(
    transcript: str = "hello world",
    confidence: float = 0.95,
    words: list | None = None,
) -> SimpleNamespace:
    if words is None:
        words = [
            _make_mock_word("hello", 0.0, 0.5, 0.95),
            _make_mock_word("world", 0.5, 1.0, 0.92),
        ]
    return SimpleNamespace(
        transcript=transcript,
        confidence=confidence,
        words=words,
    )


def _make_mock_result(
    alternatives: list | None = None,
) -> SimpleNamespace:
    if alternatives is None:
        alternatives = [_make_mock_alternative()]
    return SimpleNamespace(
        alternatives=alternatives,
    )


def _make_mock_response(
    results: list | None = None,
) -> SimpleNamespace:
    if results is None:
        results = [_make_mock_result()]
    return SimpleNamespace(results=results)


def _make_params(language: str = "en") -> TranscribeInput:
    """Build typed realtime transcribe params."""
    return TranscribeInput(language=language)


@pytest.fixture(autouse=True)
def _cleanup_injected_modules():
    """Remove dynamically loaded modules after each test to prevent ordering pollution."""
    keys_before = set(sys.modules)
    yield
    for key in list(sys.modules):
        if key not in keys_before:
            sys.modules.pop(key, None)


@pytest.fixture()
def _mock_riva_modules():
    """Mock riva.client and related modules so engine code can be imported."""
    mock_riva = MagicMock()
    mock_riva_client = MagicMock()
    mock_riva_asr_pb2 = MagicMock()

    modules = {
        "riva": mock_riva,
        "riva.client": mock_riva_client,
        "riva.client.proto": MagicMock(),
        "riva.client.proto.riva_asr_pb2": mock_riva_asr_pb2,
    }

    with patch.dict(sys.modules, modules):
        yield mock_riva_client, mock_riva_asr_pb2


def _load_module(name: str, path: Path):
    """Load a Python module from file path."""
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def riva_rt_engine_class(_mock_riva_modules):
    """Load the RivaRealtimeEngine class with mocked riva imports."""
    _load_module("riva_client", UNIFIED_RIVA_DIR / "riva_client.py")
    rt_mod = _load_module("rt_engine", UNIFIED_RIVA_DIR / "rt_engine.py")
    return rt_mod.RivaRealtimeEngine


@pytest.fixture()
def engine_with_mock(riva_rt_engine_class):
    """Create a RivaRealtimeEngine with a mocked RivaClient core."""
    engine = riva_rt_engine_class()
    engine.load_models()
    return engine


class TestRivaRtTranscript:
    """Verify Transcript contract from RT engine."""

    def test_transcribe_returns_text_and_words(self, engine_with_mock) -> None:
        engine = engine_with_mock

        response = _make_mock_response()
        engine._core.asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio, _make_params("en"))

        assert result.text == "hello world"
        assert len(result.segments) == 1
        assert len(result.segments[0].words) == 2
        assert result.segments[0].words[0].text == "hello"
        assert result.segments[0].words[0].start == 0.0
        assert result.segments[0].words[0].end == 0.5
        assert result.language == "en"
        assert result.language_confidence == 0.95

    def test_transcribe_handles_auto_language(self, engine_with_mock) -> None:
        engine = engine_with_mock

        response = _make_mock_response()
        engine._core.asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio, _make_params("auto"))

        assert result.language == "en"

    def test_transcribe_passes_language_code(self, engine_with_mock) -> None:
        engine = engine_with_mock

        response = _make_mock_response()
        engine._core.asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio, _make_params("es"))

        assert result.language == "es"

    def test_transcribe_empty_response(self, engine_with_mock) -> None:
        engine = engine_with_mock

        response = _make_mock_response(results=[])
        engine._core.asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio, _make_params("en"))

        assert result.text == ""
        assert result.segments == []
        assert result.language_confidence == 0.0

    def test_transcribe_converts_float32_to_int16(self, engine_with_mock) -> None:
        engine = engine_with_mock

        response = _make_mock_response()
        engine._core.asr.offline_recognize.return_value = response

        audio = np.array([0.5, -0.5, 1.0, -1.0], dtype=np.float32)
        engine.transcribe(audio, _make_params("en"))

        call_args = engine._core.asr.offline_recognize.call_args
        audio_bytes = call_args[0][0]

        # 4 samples * 2 bytes = 8 bytes
        assert len(audio_bytes) == 8

    def test_transcribe_multiple_results(self, engine_with_mock) -> None:
        engine = engine_with_mock

        response = _make_mock_response(
            results=[
                _make_mock_result(
                    alternatives=[
                        _make_mock_alternative(
                            transcript="hello",
                            words=[_make_mock_word("hello", 0.0, 0.5)],
                        )
                    ]
                ),
                _make_mock_result(
                    alternatives=[
                        _make_mock_alternative(
                            transcript="world",
                            words=[_make_mock_word("world", 0.5, 1.0)],
                        )
                    ]
                ),
            ]
        )
        engine._core.asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio, _make_params("en"))

        assert result.text == "hello world"
        words = [w for seg in result.segments for w in (seg.words or [])]
        assert len(words) == 2


class TestRivaRtStreamingSupport:
    """Verify streaming behavior configuration."""

    def test_supports_native_streaming_returns_true(self, engine_with_mock) -> None:
        assert engine_with_mock.supports_native_streaming() is True

    def test_get_engine_id_returns_env_value(self, engine_with_mock) -> None:
        assert engine_with_mock.get_engine_id() == "riva"

    def test_get_engine_id_respects_env_override(
        self, riva_rt_engine_class, monkeypatch
    ) -> None:
        monkeypatch.setenv("DALSTON_ENGINE_ID", "riva-nim-2")
        engine = riva_rt_engine_class()
        assert engine.get_engine_id() == "riva-nim-2"

    def test_get_models_returns_empty(self, engine_with_mock) -> None:
        assert engine_with_mock.get_models() == []

    def test_get_vocabulary_support(self, engine_with_mock) -> None:
        vocab = engine_with_mock.get_vocabulary_support()
        assert vocab.method.value == "word_boosting"
        assert vocab.batch is True
        assert vocab.realtime is True

    def test_get_gpu_memory_usage_zero(self, engine_with_mock) -> None:
        assert engine_with_mock.get_gpu_memory_usage() == "0GB"


class TestRivaRtHealthCheck:
    """Verify health check behavior."""

    def test_health_check_includes_nim_status(self, engine_with_mock) -> None:
        engine = engine_with_mock
        engine._core.asr.stub.GetRivaSpeechRecognitionConfig.return_value = MagicMock()

        health = engine.health_check()
        assert "nim" in health
        assert health["nim"] == "connected"
        assert "uri" in health

    def test_health_check_nim_unreachable(self, engine_with_mock) -> None:
        engine = engine_with_mock

        import grpc as mock_grpc

        engine._core.asr.stub.GetRivaSpeechRecognitionConfig.side_effect = (
            mock_grpc.RpcError()
        )

        health = engine.health_check()
        assert health["nim"] == "unreachable"


class TestRivaRtInitialization:
    """Verify engine initialization."""

    def test_load_models_creates_core(self, riva_rt_engine_class) -> None:
        engine = riva_rt_engine_class()
        assert engine._core is None

        engine.load_models()
        assert engine._core is not None

    def test_transcribe_raises_without_load_models(self, riva_rt_engine_class) -> None:
        engine = riva_rt_engine_class()

        audio = np.zeros(16000, dtype=np.float32)
        with pytest.raises(RuntimeError, match="not initialized"):
            engine.transcribe(audio, _make_params("en"))

    def test_shutdown_does_not_raise(self, engine_with_mock) -> None:
        engine_with_mock.shutdown()

    def test_shutdown_idempotent(self, riva_rt_engine_class) -> None:
        engine = riva_rt_engine_class()
        # shutdown before load_models should not raise
        engine.shutdown()


class TestRivaRtWordConfidence:
    """Verify per-word confidence from Riva response."""

    def test_word_confidence_uses_per_word_value(self, engine_with_mock) -> None:
        engine = engine_with_mock

        words = [
            _make_mock_word("hello", 0.0, 0.5, 0.99),
            _make_mock_word("world", 0.5, 1.0, 0.85),
        ]
        alt = _make_mock_alternative(confidence=0.90, words=words)
        response = _make_mock_response(results=[_make_mock_result(alternatives=[alt])])
        engine._core.asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio, _make_params("en"))

        words = result.segments[0].words
        assert words[0].confidence == 0.99
        assert words[1].confidence == 0.85
