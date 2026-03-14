"""Contract tests for parakeet (NeMo) RT engine.

Verifies that the RT engine produces the correct Transcript
shape and that word timestamp behavior is preserved after delegation
to NemoInference.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from dalston.common.pipeline_types import TranscribeInput
from dalston.engine_sdk.inference.nemo_inference import (
    NemoInference,
    NeMoSegmentResult,
    NeMoTranscriptionResult,
    NeMoWordResult,
)

torch = pytest.importorskip("torch")


def _make_core_result(
    text: str = "hello world",
    words: list[NeMoWordResult] | None = None,
) -> NeMoTranscriptionResult:
    """Create a mock NemoInference transcription result."""
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
    """Create a NemoRealtimeEngine with a mocked core."""
    import importlib.util
    import sys
    from pathlib import Path

    engine_path = Path("engines/stt-unified/nemo/rt_engine.py")
    spec = importlib.util.spec_from_file_location("m63_parakeet_rt", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_parakeet_rt"] = module
    spec.loader.exec_module(module)

    mock_core = MagicMock(spec=NemoInference)
    mock_core.device = "cpu"
    mock_core.transcribe.return_value = core_result
    mock_core.manager = MagicMock()

    engine = module.NemoRealtimeEngine(core=mock_core)
    return engine


class TestParakeetRTOutputShape:
    """Verify Transcript structure from parakeet RT engine."""

    def test_output_has_text_and_language(self) -> None:
        result = _make_core_result(text="hello world")
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-tdt-1.1b"),
        )

        assert output.text == "hello world"
        assert output.language == "en"
        assert output.language_confidence == 1.0

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
        output = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-tdt-1.1b"),
        )

        words = [w for seg in output.segments for w in (seg.words or [])]
        assert len(words) == 2
        assert words[0].text == "hello"
        assert words[0].start == 0.0
        assert words[1].text == "world"

    def test_empty_transcription(self) -> None:
        result = NeMoTranscriptionResult(text="", segments=[])
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-tdt-1.1b"),
        )

        assert output.text == ""
        assert output.segments == []

    def test_model_normalization(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="nvidia/parakeet-tdt-1.1b"),
        )

        # Should normalize to NeMoModelManager format
        call_args = engine._core.transcribe.call_args
        assert call_args[0][1] == "parakeet-tdt-1.1b"


class TestParakeetRTEngineMetadata:
    """Verify engine metadata methods."""

    def test_get_engine_id(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.get_engine_id() == "nemo"

    def test_supports_native_streaming(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.supports_native_streaming() is True

    def test_get_vocabulary_support(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        vocab_support = engine.get_vocabulary_support()
        assert vocab_support.method == "phrase_boosting"
        assert vocab_support.batch is True
        assert vocab_support.realtime is False
        assert vocab_support.supported is True

    def test_get_models(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        models = engine.get_models()
        assert isinstance(models, list)
        assert len(models) > 0
