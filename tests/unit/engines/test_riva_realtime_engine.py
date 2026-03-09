"""Unit tests for Riva NIM realtime engine gRPC result mapping.

Tests verify the mapping from Riva gRPC responses to Dalston's
TranscribeResult without requiring a live Riva NIM container.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.realtime_sdk.assembler import TranscribeResult


def _make_word(word: str, start: float, end: float, confidence: float = 0.95):
    return SimpleNamespace(
        word=word, start_time=start, end_time=end, confidence=confidence
    )


def _make_alternative(transcript: str, words: list, confidence: float = 0.95):
    return SimpleNamespace(transcript=transcript, words=words, confidence=confidence)


def _make_result(alternatives: list):
    return SimpleNamespace(alternatives=alternatives)


def _make_response(results: list):
    return SimpleNamespace(results=results)


@pytest.fixture
def riva_rt_engine():
    """Create a RivaRealtimeEngine with mocked gRPC client."""
    mock_riva = MagicMock()
    mock_auth = MagicMock()
    mock_asr = MagicMock()
    mock_riva.Auth.return_value = mock_auth
    mock_riva.ASRService.return_value = mock_asr

    with patch.dict(sys.modules, {"riva": MagicMock(), "riva.client": mock_riva}):
        with patch.dict(
            "os.environ",
            {
                "RIVA_GRPC_URL": "localhost:50051",
                "DALSTON_INSTANCE": "test-riva-rt",
                "REDIS_URL": "redis://localhost:6379",
            },
        ):
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "riva_rt_engine",
                "engines/stt-rt/riva/engine.py",
            )
            module = importlib.util.module_from_spec(spec)
            module.riva = MagicMock()
            module.riva.client = mock_riva
            spec.loader.exec_module(module)

            engine = module.RivaRealtimeEngine()
            engine._asr = mock_asr
            yield engine

    sys.modules.pop("riva_rt_engine", None)


class TestRivaRTTranscribe:
    """Test transcribe() mapping from Riva response to TranscribeResult."""

    def test_single_utterance(self, riva_rt_engine):
        words = [_make_word("Hello", 0.0, 0.3), _make_word("world", 0.4, 0.7)]
        response = _make_response(
            [_make_result([_make_alternative("Hello world", words, confidence=0.92)])]
        )
        riva_rt_engine._asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = riva_rt_engine.transcribe(audio, "en", "nvidia/parakeet-ctc-1.1b-riva")

        assert isinstance(result, TranscribeResult)
        assert result.text == "Hello world"
        assert len(result.words) == 2
        assert result.words[0].word == "Hello"
        assert result.words[1].word == "world"
        assert result.confidence == 0.92

    def test_empty_response(self, riva_rt_engine):
        response = _make_response([])
        riva_rt_engine._asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = riva_rt_engine.transcribe(audio, "en", "model")

        assert result.text == ""
        assert len(result.words) == 0
        assert result.confidence == 0.0

    def test_auto_language_detection(self, riva_rt_engine):
        words = [_make_word("test", 0.0, 0.3)]
        response = _make_response([_make_result([_make_alternative("test", words)])])
        riva_rt_engine._asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = riva_rt_engine.transcribe(audio, "auto", "model")

        assert result.language == "en"

    def test_audio_conversion_int16(self, riva_rt_engine):
        """Verify float32 → int16 PCM conversion."""
        words = [_make_word("x", 0.0, 0.1)]
        response = _make_response([_make_result([_make_alternative("x", words)])])
        riva_rt_engine._asr.offline_recognize.return_value = response

        # Create a known audio signal
        audio = np.array([0.5, -0.5, 1.0, -1.0], dtype=np.float32)
        riva_rt_engine.transcribe(audio, "en", "model")

        # Verify the audio bytes passed to Riva
        call_args = riva_rt_engine._asr.offline_recognize.call_args
        audio_bytes = call_args[0][0]

        # Reconstruct int16 from bytes
        reconstructed = np.frombuffer(audio_bytes, dtype=np.int16)
        expected = (audio * 32768).astype(np.int16)
        np.testing.assert_array_equal(reconstructed, expected)

    def test_word_confidence_mapping(self, riva_rt_engine):
        words = [_make_word("hi", 0.0, 0.2, confidence=0.88)]
        response = _make_response(
            [_make_result([_make_alternative("hi", words, confidence=0.91)])]
        )
        riva_rt_engine._asr.offline_recognize.return_value = response

        audio = np.zeros(16000, dtype=np.float32)
        result = riva_rt_engine.transcribe(audio, "en", "model")

        assert result.words[0].confidence == 0.88
        assert result.confidence == 0.91

    def test_multi_result_concatenation(self, riva_rt_engine):
        words1 = [_make_word("First", 0.0, 0.3)]
        words2 = [_make_word("second", 1.0, 1.3)]
        response = _make_response(
            [
                _make_result([_make_alternative("First", words1)]),
                _make_result([_make_alternative("second", words2)]),
            ]
        )
        riva_rt_engine._asr.offline_recognize.return_value = response

        audio = np.zeros(32000, dtype=np.float32)
        result = riva_rt_engine.transcribe(audio, "en", "model")

        assert result.text == "First second"
        assert len(result.words) == 2


class TestRivaRTMetadata:
    """Test engine metadata methods."""

    def test_get_runtime(self, riva_rt_engine):
        assert riva_rt_engine.get_runtime() == "riva"

    def test_get_languages(self, riva_rt_engine):
        assert riva_rt_engine.get_languages() == ["en"]

    def test_get_models(self, riva_rt_engine):
        assert riva_rt_engine.get_models() == ["nvidia/parakeet-ctc-1.1b-riva"]

    def test_supports_streaming_false(self, riva_rt_engine):
        assert riva_rt_engine.supports_streaming() is False

    def test_supports_vocabulary_false(self, riva_rt_engine):
        assert riva_rt_engine.get_supports_vocabulary() is False
