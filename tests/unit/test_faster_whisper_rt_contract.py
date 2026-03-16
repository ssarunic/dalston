"""Contract tests for faster-whisper realtime engine.

Verifies that the RT engine produces the correct output shape
(Transcript with text, segments, language) and
that session lifecycle behavior is preserved after delegation
to FasterWhisperInference.

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

from dalston.common.pipeline_types import TranscriptionRequest


@pytest.fixture(autouse=True)
def _cleanup_injected_modules():
    """Clean up modules injected by importlib to prevent test pollution."""
    keys_before = set(sys.modules)
    yield
    for key in list(sys.modules):
        if key not in keys_before:
            sys.modules.pop(key, None)


def _load_rt_engine():
    """Load FasterWhisperRealtimeEngine from file to avoid import path issues."""
    engine_path = Path("engines/stt-unified/faster-whisper/rt_engine.py")
    spec = importlib.util.spec_from_file_location("m63_whisper_rt_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_whisper_rt_engine"] = module
    spec.loader.exec_module(module)
    return module.FasterWhisperRealtimeEngine


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
    """Create a FasterWhisperRealtimeEngine with a mocked FasterWhisperInference."""
    FasterWhisperRealtimeEngine = _load_rt_engine()
    engine = FasterWhisperRealtimeEngine()

    # Simulate load_models() by creating a mock core
    from dalston.engine_sdk.inference.faster_whisper_inference import (
        FasterWhisperInference,
        SegmentResult,
        TranscriptionResult,
        WordResult,
    )

    engine._core = MagicMock(spec=FasterWhisperInference)
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


def _make_params(
    language: str = "auto",
    model_variant: str = "large-v3-turbo",
    vocabulary: list[str] | None = None,
) -> TranscriptionRequest:
    """Create typed transcribe params for RT calls."""
    return TranscriptionRequest(
        language=language,
        loaded_model_id=model_variant,
        vocabulary=vocabulary,
    )


class TestRTOutputShape:
    """Verify Transcript structure from RT engine."""

    def test_output_has_text_words_language_confidence(self) -> None:
        segments = [_make_mock_segment(text="hello world")]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(_make_audio(), _make_params(language="auto"))

        assert result.text == "hello world"
        assert result.language == "en"
        assert result.language_confidence == 0.99
        words = [w for seg in result.segments for w in (seg.words or [])]
        assert len(words) == 2

    def test_word_timestamps_in_result(self) -> None:
        mock_words = [
            SimpleNamespace(word=" hello", start=0.0, end=0.5, probability=0.95),
            SimpleNamespace(word=" world", start=0.5, end=1.0, probability=0.92),
        ]
        segments = [_make_mock_segment(words=mock_words)]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(_make_audio(), _make_params(language="en"))

        words = [w for seg in result.segments for w in (seg.words or [])]
        assert len(words) == 2
        assert words[0].text == "hello"
        assert words[0].start == 0.0
        assert words[0].end == 0.5
        assert words[0].confidence == 0.95
        assert words[1].text == "world"

    def test_empty_audio_returns_empty_result(self) -> None:
        segments: list = []
        info = _make_mock_info(duration=0.0)
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(_make_audio(0.0), _make_params(language="auto"))

        assert result.text == ""
        assert result.segments == []

    def test_multiple_segments_concatenated(self) -> None:
        segments = [
            _make_mock_segment(start=0.0, end=1.0, text="hello"),
            _make_mock_segment(start=1.0, end=2.0, text="world"),
        ]
        info = _make_mock_info(duration=2.0)
        engine = _build_rt_engine_with_mock(segments, info)

        result = engine.transcribe(_make_audio(2.0), _make_params(language="en"))

        assert result.text == "hello world"


class TestRTConfigPassthrough:
    """Verify config values reach FasterWhisperInference correctly."""

    def test_language_auto_passed_through(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(_make_audio(), _make_params(language="auto"))

        # Verify core.transcribe was called with correct config
        call_kwargs = engine._core.transcribe.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.language == "auto"

    def test_vad_filter_is_false_for_rt(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(_make_audio(), _make_params(language="en"))

        call_kwargs = engine._core.transcribe.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.vad_filter is False

    def test_vocabulary_passed_as_initial_prompt(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            _make_audio(),
            _make_params(
                language="en",
                model_variant="large-v3-turbo",
                vocabulary=["Dalston", "Redis"],
            ),
        )

        call_kwargs = engine._core.transcribe.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.initial_prompt == "Dalston, Redis"

    def test_model_alias_normalization(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            _make_audio(),
            _make_params(language="en", model_variant="faster-whisper-large-v3"),
        )

        # Core should have been called — model normalization happens inside core
        assert engine._core.transcribe.called

    def test_default_model_when_none(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_rt_engine_with_mock(segments, info)

        engine.transcribe(
            _make_audio(),
            _make_params(language="en", model_variant=""),
        )

        call_kwargs = engine._core.transcribe.call_args
        model_id = call_kwargs.kwargs.get("model_id") or call_kwargs[1].get("model_id")
        assert model_id == "large-v3-turbo"


class TestRTEngineMetadata:
    """Verify engine metadata methods."""

    def test_get_engine_id(self) -> None:
        engine = _load_rt_engine()()
        assert engine.get_engine_id() == "faster-whisper"

    def test_get_models(self) -> None:
        engine = _load_rt_engine()()
        models = engine.get_models()
        assert "large-v3-turbo" in models
        assert "large-v3" in models
        assert len(models) >= 5

    def test_get_vocabulary_support(self) -> None:
        engine = _load_rt_engine()()
        vocab = engine.get_vocabulary_support()
        assert vocab.method.value == "prompt_conditioning"
        assert vocab.batch is True
        assert vocab.realtime is True
