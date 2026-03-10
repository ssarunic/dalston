"""Contract tests for faster-whisper realtime engine.

Verifies that the RT engine produces the correct output shape
(TranscribeResult with text, words, language, confidence) and
that session lifecycle behavior is preserved after delegation
to TranscribeCore.

These tests mock the faster-whisper model to avoid GPU/model dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _cleanup_injected_modules():
    """Clean up modules injected by importlib to prevent test pollution."""
    keys_before = set(sys.modules)
    yield
    for key in list(sys.modules):
        if key not in keys_before:
            sys.modules.pop(key, None)


def _load_rt_engine():
    """Load WhisperStreamingEngine from file to avoid import path issues."""
    engine_path = Path("engines/stt-rt/faster-whisper/engine.py")
    spec = importlib.util.spec_from_file_location("m63_whisper_rt_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_whisper_rt_engine"] = module
    spec.loader.exec_module(module)
    return module.WhisperStreamingEngine


def _make_mock_segment(
    start: float = 0.0,
    end: float = 1.0,
    text: str = "hello",
    words: list | None = None,
) -> SimpleNamespace:
    """Create a mock faster-whisper segment for RT."""
    if words is None:
        words = [
            SimpleNamespace(word=" hello", start=0.0, end=0.5, probability=0.95),
            SimpleNamespace(word=" world", start=0.5, end=1.0, probability=0.92),
        ]
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=words,
        tokens=None,
        avg_logprob=None,
        compression_ratio=None,
        no_speech_prob=None,
    )


def _make_mock_info(
    language: str = "en",
    language_probability: float = 0.99,
    duration: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        language=language,
        language_probability=language_probability,
        duration=duration,
    )


def _build_rt_engine_with_mock(segments, info):
    """Create a WhisperStreamingEngine with a mocked TranscribeCore."""
    WhisperStreamingEngine = _load_rt_engine()
    engine = WhisperStreamingEngine()

    # Simulate load_models() by creating a mock core
    from dalston.engine_sdk.cores.faster_whisper_core import (
        SegmentResult,
        TranscribeCore,
        TranscriptionResult,
        WordResult,
    )

    engine._core = MagicMock(spec=TranscribeCore)
    engine._core.manager = MagicMock()
    engine._core.manager.model_storage = None
    engine._core.device = "cpu"
    engine._core.compute_type = "int8"

    core_segments = []
    for seg in segments:
        words = []
        if seg.words:
            words = [
                WordResult(
                    word=w.word.strip(),
                    start=round(w.start, 3),
                    end=round(w.end, 3),
                    probability=round(w.probability, 3),
                )
                for w in seg.words
            ]
        core_segments.append(
            SegmentResult(
                start=round(seg.start, 3),
                end=round(seg.end, 3),
                text=seg.text.strip(),
                words=words,
            )
        )

    engine._core.transcribe.return_value = TranscriptionResult(
        segments=core_segments,
        language=info.language,
        language_probability=info.language_probability,
        duration=info.duration,
    )

    # Set up model manager for heartbeat
    engine._model_manager = MagicMock()

    return engine


def _make_audio(duration_seconds: float = 1.0, sample_rate: int = 16000) -> np.ndarray:
    """Create a mock audio array."""
    return np.zeros(int(duration_seconds * sample_rate), dtype=np.float32)


class TestRTOutputShape:
    """Verify TranscribeResult structure from RT engine."""

    def test_output_has_text_words_language_confidence(self) -> None:
        segments = [_make_mock_segment(text="hello world")]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(
            audio=_make_audio(),
            language="auto",
            model_variant="large-v3-turbo",
        )

        assert result.text == "hello world"
        assert result.language == "en"
        assert result.confidence == 0.99
        assert len(result.words) == 2

    def test_word_timestamps_in_result(self) -> None:
        mock_words = [
            SimpleNamespace(word=" hello", start=0.0, end=0.5, probability=0.95),
            SimpleNamespace(word=" world", start=0.5, end=1.0, probability=0.92),
        ]
        segments = [_make_mock_segment(words=mock_words)]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(
            audio=_make_audio(),
            language="en",
            model_variant="large-v3-turbo",
        )

        assert len(result.words) == 2
        assert result.words[0].word == "hello"
        assert result.words[0].start == 0.0
        assert result.words[0].end == 0.5
        assert result.words[0].confidence == 0.95
        assert result.words[1].word == "world"

    def test_empty_audio_returns_empty_result(self) -> None:
        segments: list = []
        info = _make_mock_info(duration=0.0)
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(
            audio=_make_audio(0.0),
            language="auto",
            model_variant="large-v3-turbo",
        )

        assert result.text == ""
        assert result.words == []

    def test_multiple_segments_concatenated(self) -> None:
        segments = [
            _make_mock_segment(start=0.0, end=1.0, text="hello"),
            _make_mock_segment(start=1.0, end=2.0, text="world"),
        ]
        info = _make_mock_info(duration=2.0)
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(
            audio=_make_audio(2.0),
            language="en",
            model_variant="large-v3-turbo",
        )

        assert result.text == "hello world"


class TestRTConfigPassthrough:
    """Verify config values reach TranscribeCore correctly."""

    def test_language_auto_passed_through(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            audio=_make_audio(),
            language="auto",
            model_variant="large-v3-turbo",
        )

        # Verify core.transcribe was called with correct config
        call_kwargs = engine._core.transcribe.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.language == "auto"

    def test_vad_filter_is_false_for_rt(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            audio=_make_audio(),
            language="en",
            model_variant="large-v3-turbo",
        )

        call_kwargs = engine._core.transcribe.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.vad_filter is False

    def test_vocabulary_passed_as_initial_prompt(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            audio=_make_audio(),
            language="en",
            model_variant="large-v3-turbo",
            vocabulary=["Dalston", "Redis"],
        )

        call_kwargs = engine._core.transcribe.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.initial_prompt == "Dalston, Redis"

    def test_model_alias_normalization(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            audio=_make_audio(),
            language="en",
            model_variant="faster-whisper-large-v3",
        )

        # Core should have been called — model normalization happens inside core
        assert engine._core.transcribe.called

    def test_default_model_when_none(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            audio=_make_audio(),
            language="en",
            model_variant="",
        )

        call_kwargs = engine._core.transcribe.call_args
        model_id = call_kwargs.kwargs.get("model_id") or call_kwargs[1].get("model_id")
        assert model_id == "large-v3-turbo"


class TestRTEngineMetadata:
    """Verify engine metadata methods."""

    def test_get_runtime(self) -> None:
        engine = _load_rt_engine()()
        assert engine.get_runtime() == "faster-whisper"

    def test_get_models(self) -> None:
        engine = _load_rt_engine()()
        models = engine.get_models()
        assert "large-v3-turbo" in models
        assert "large-v3" in models
        assert len(models) >= 5

    def test_get_supports_vocabulary(self) -> None:
        engine = _load_rt_engine()()
        assert engine.get_supports_vocabulary() is True

    def test_get_languages(self) -> None:
        engine = _load_rt_engine()()
        assert engine.get_languages() == ["auto"]
