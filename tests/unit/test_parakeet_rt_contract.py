"""Contract tests for parakeet (NeMo) RT engine.

Verifies that the RT engine produces the correct TranscribeResult
shape and that word timestamp behavior is preserved after delegation
to ParakeetCore.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from dalston.engine_sdk.cores.parakeet_core import (
    NeMoSegmentResult,
    NeMoTranscriptionResult,
    NeMoWordResult,
    ParakeetCore,
)

torch = pytest.importorskip("torch")


def _make_core_result(
    text: str = "hello world",
    words: list[NeMoWordResult] | None = None,
) -> NeMoTranscriptionResult:
    """Create a mock ParakeetCore transcription result."""
    if words is None:
        words = [
            NeMoWordResult(word="hello", start=0.0, end=0.5),
            NeMoWordResult(word="world", start=0.5, end=1.0),
        ]
    segments = [
        NeMoSegmentResult(
            start=words[0].start if words else 0.0,
            end=words[-1].end if words else 0.0,
            text=text,
            words=words,
        )
    ]
    return NeMoTranscriptionResult(
        text=text,
        segments=segments,
        language="en",
        language_probability=1.0,
    )


def _build_rt_engine(core_result: NeMoTranscriptionResult):
    """Create a ParakeetStreamingEngine with a mocked core."""
    import importlib.util
    import sys
    from pathlib import Path

    engine_path = Path("engines/stt-rt/parakeet/engine.py")
    spec = importlib.util.spec_from_file_location("m63_parakeet_rt", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_parakeet_rt"] = module
    spec.loader.exec_module(module)

    mock_core = MagicMock(spec=ParakeetCore)
    mock_core.device = "cpu"
    mock_core.transcribe.return_value = core_result
    mock_core.manager = MagicMock()

    engine = module.ParakeetStreamingEngine(core=mock_core)
    return engine


class TestParakeetRTOutputShape:
    """Verify TranscribeResult structure from parakeet RT engine."""

    def test_output_has_text_and_language(self) -> None:
        result = _make_core_result(text="hello world")
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(audio, "en", "parakeet-tdt-1.1b")

        assert output.text == "hello world"
        assert output.language == "en"
        assert output.confidence == 1.0

    def test_output_has_words(self) -> None:
        result = _make_core_result(
            text="hello world",
            words=[
                NeMoWordResult(word="hello", start=0.0, end=0.5),
                NeMoWordResult(word="world", start=0.5, end=1.0),
            ],
        )
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(audio, "en", "parakeet-tdt-1.1b")

        assert len(output.words) == 2
        assert output.words[0].word == "hello"
        assert output.words[0].start == 0.0
        assert output.words[1].word == "world"

    def test_empty_transcription(self) -> None:
        result = NeMoTranscriptionResult(text="", segments=[])
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(audio, "en", "parakeet-tdt-1.1b")

        assert output.text == ""
        assert output.words == []

    def test_model_normalization(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        engine.transcribe(audio, "en", "nvidia/parakeet-tdt-1.1b")

        # Should normalize to NeMoModelManager format
        call_args = engine._core.transcribe.call_args
        assert call_args[0][1] == "parakeet-tdt-1.1b"


class TestParakeetRTEngineMetadata:
    """Verify engine metadata methods."""

    def test_get_runtime(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.get_runtime() == "nemo"

    def test_get_languages(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.get_languages() == ["en"]

    def test_supports_streaming(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.supports_streaming() is True

    def test_get_supports_vocabulary(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.get_supports_vocabulary() is False

    def test_get_models(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        models = engine.get_models()
        assert isinstance(models, list)
        assert len(models) > 0
