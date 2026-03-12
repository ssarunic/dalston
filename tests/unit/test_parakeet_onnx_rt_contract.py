"""Contract tests for parakeet-onnx RT engine.

Verifies that the RT engine produces the correct Transcript
shape and that word timestamp behavior is preserved after delegation
to OnnxInference.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from dalston.common.pipeline_types import TranscribeInput
from dalston.engine_sdk.inference.onnx_inference import (
    OnnxInference,
    OnnxSegmentResult,
    OnnxTranscriptionResult,
    OnnxWordResult,
)


def _make_core_result(
    text: str = "hello world",
    words: list[OnnxWordResult] | None = None,
) -> OnnxTranscriptionResult:
    if words is None:
        words = [
            OnnxWordResult(word="hello", start=0.0, end=0.5),
            OnnxWordResult(word="world", start=0.5, end=1.0),
        ]
    segments = [
        OnnxSegmentResult(
            start=words[0].start if words else 0.0,
            end=words[-1].end if words else 0.0,
            text=text,
            words=words,
        )
    ]
    return OnnxTranscriptionResult(
        text=text,
        segments=segments,
        language="en",
        language_probability=1.0,
    )


def _build_rt_engine(core_result: OnnxTranscriptionResult):
    engine_path = Path("engines/stt-rt/onnx/engine.py")
    spec = importlib.util.spec_from_file_location("m63_parakeet_onnx_rt", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_parakeet_onnx_rt"] = module
    spec.loader.exec_module(module)

    mock_core = MagicMock(spec=OnnxInference)
    mock_core.device = "cpu"
    mock_core.quantization = "none"
    mock_core.transcribe.return_value = core_result
    mock_core.manager = MagicMock()

    engine = module.OnnxRealtimeEngine(core=mock_core)
    return engine


class TestOnnxRTOutputShape:
    """Verify Transcript structure from ONNX RT engine."""

    def test_output_has_text_and_language(self) -> None:
        result = _make_core_result(text="hello world")
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-onnx-ctc-0.6b"),
        )

        assert output.text == "hello world"
        assert output.language == "en"
        assert output.language_confidence == 1.0

    def test_output_has_words(self) -> None:
        result = _make_core_result(
            text="hello world",
            words=[
                OnnxWordResult(word="hello", start=0.0, end=0.5),
                OnnxWordResult(word="world", start=0.5, end=1.0),
            ],
        )
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-onnx-ctc-0.6b"),
        )

        words = [w for seg in output.segments for w in (seg.words or [])]
        assert len(words) == 2
        assert words[0].text == "hello"
        assert words[0].start == 0.0
        assert words[1].text == "world"

    def test_empty_transcription(self) -> None:
        result = OnnxTranscriptionResult(text="", segments=[])
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        output = engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-onnx-ctc-0.6b"),
        )

        assert output.text == ""
        assert output.segments == []

    def test_model_normalization(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)

        audio = np.zeros(16000, dtype=np.float32)
        engine.transcribe(
            audio,
            TranscribeInput(language="en", loaded_model_id="parakeet-onnx-tdt-0.6b-v3"),
        )

        call_args = engine._core.transcribe.call_args
        assert call_args[0][1] == "parakeet-onnx-tdt-0.6b-v3"


class TestOnnxRTEngineMetadata:
    """Verify engine metadata methods."""

    def test_get_engine_id(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.get_engine_id() == "onnx"

    def test_get_languages(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.get_languages() == ["en"]

    def test_supports_streaming(self) -> None:
        result = _make_core_result()
        engine = _build_rt_engine(result)
        assert engine.supports_streaming() is False

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
