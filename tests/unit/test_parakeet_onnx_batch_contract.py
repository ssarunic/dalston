"""Contract tests for parakeet-onnx batch engine.

Verifies that the batch engine produces the correct output shape
(Transcript with segments, text, language) and that word
timestamp behavior is preserved after delegation to OnnxInference.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from dalston.engine_sdk import EngineInput
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.inference.onnx_inference import (
    OnnxInference,
    OnnxSegmentResult,
    OnnxTranscriptionResult,
    OnnxWordResult,
)


def _ctx(task_id: str, job_id: str) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="test-engine_id",
        instance="test-instance",
        task_id=task_id,
        job_id=job_id,
        stage="transcribe",
    )


def _load_onnx_engine_class():
    engine_path = Path("engines/stt-transcribe/parakeet-onnx/engine.py")
    spec = importlib.util.spec_from_file_location(
        "m63_parakeet_onnx_engine", engine_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_parakeet_onnx_engine"] = module
    spec.loader.exec_module(module)
    return module.OnnxBatchEngine


_ParakeetOnnxEngine = _load_onnx_engine_class()


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


def _build_engine_with_mock_core(core_result: OnnxTranscriptionResult):
    mock_core = MagicMock(spec=OnnxInference)
    mock_core.device = "cpu"
    mock_core.quantization = "none"
    mock_core.transcribe.return_value = core_result
    mock_core.get_stats.return_value = {}

    with patch.object(_ParakeetOnnxEngine, "__init__", lambda self, core=None: None):
        engine = _ParakeetOnnxEngine.__new__(_ParakeetOnnxEngine)
        engine._core = mock_core
        engine._default_model_id = "parakeet-onnx-ctc-0.6b"
        engine._engine_id = "onnx"
        engine.logger = MagicMock()
    return engine


class TestOnnxBatchOutputShape:
    """Verify Transcript structure from ONNX batch engine."""

    def test_output_has_text_and_language(self) -> None:
        result = _make_core_result(text="hello world")
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        data = output.data
        assert data.text == "hello world"
        assert data.language == "en"
        assert data.language_confidence == 1.0
        assert len(data.segments) >= 1
        assert data.engine_id == "onnx"

    def test_output_has_word_timestamps(self) -> None:
        result = _make_core_result(
            text="hello world",
            words=[
                OnnxWordResult(word="hello", start=0.0, end=0.5),
                OnnxWordResult(word="world", start=0.5, end=1.0),
            ],
        )
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        seg = output.data.segments[0]
        assert seg.words is not None
        assert len(seg.words) == 2
        assert seg.words[0].text == "hello"
        assert seg.words[1].text == "world"

    def test_empty_result(self) -> None:
        result = OnnxTranscriptionResult(text="", segments=[])
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert output.data.text == ""
        assert output.data.segments == []


class TestOnnxBatchAlignmentMethod:
    """Verify alignment method based on decoder type."""

    def test_ctc_alignment(self) -> None:
        result = _make_core_result()
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"loaded_model_id": "parakeet-onnx-ctc-0.6b"},
            ),
            _ctx(task_id, job_id),
        )

        assert output.data.alignment_method.value == "ctc"

    def test_tdt_alignment(self) -> None:
        result = _make_core_result()
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"loaded_model_id": "parakeet-onnx-tdt-0.6b-v3"},
            ),
            _ctx(task_id, job_id),
        )

        assert output.data.alignment_method.value == "tdt"

    def test_rnnt_alignment(self) -> None:
        result = _make_core_result()
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"loaded_model_id": "parakeet-onnx-rnnt-0.6b"},
            ),
            _ctx(task_id, job_id),
        )

        assert output.data.alignment_method.value == "rnnt"


class TestOnnxBatchTimestampGranularity:
    """Verify timestamp granularity reporting."""

    def test_word_granularity(self) -> None:
        result = _make_core_result()
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert output.data.timestamp_granularity.value == "word"

    def test_segment_granularity_when_no_words(self) -> None:
        result = OnnxTranscriptionResult(
            text="hello",
            segments=[OnnxSegmentResult(start=0.0, end=1.0, text="hello", words=[])],
        )
        engine = _build_engine_with_mock_core(result)

        task_id = str(uuid4())
        job_id = str(uuid4())
        output = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert output.data.timestamp_granularity.value == "segment"
