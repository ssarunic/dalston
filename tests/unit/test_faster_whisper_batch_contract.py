"""Contract tests for faster-whisper batch engine.

Verifies that the batch engine produces the correct output shape
(Transcript with segments, text, language) and that word
timestamp behavior is preserved after delegation to FasterWhisperInference.

These tests mock the faster-whisper model to avoid GPU/model dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from dalston.engine_sdk import EngineInput
from dalston.engine_sdk.context import BatchTaskContext


def _ctx(task_id: str, job_id: str) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="test-engine_id",
        instance="test-instance",
        task_id=task_id,
        job_id=job_id,
        stage="transcribe",
    )


def _load_whisper_engine_class():
    """Load FasterWhisperBatchEngine class once from file to avoid import path issues."""
    engine_path = Path("engines/stt-unified/faster-whisper/batch_engine.py")
    spec = importlib.util.spec_from_file_location("m63_whisper_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m63_whisper_engine"] = module
    spec.loader.exec_module(module)
    return module.FasterWhisperBatchEngine


# Load once at module level to avoid re-importing native C extensions
_WhisperEngine = _load_whisper_engine_class()


def _make_mock_segment(
    start: float = 0.0,
    end: float = 1.0,
    text: str = "hello",
    words: list | None = None,
    tokens: list[int] | None = None,
    avg_logprob: float | None = -0.25,
    compression_ratio: float | None = 1.5,
    no_speech_prob: float | None = 0.01,
) -> SimpleNamespace:
    """Create a mock faster-whisper segment."""
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
        tokens=tokens or [1, 2, 3],
        avg_logprob=avg_logprob,
        compression_ratio=compression_ratio,
        no_speech_prob=no_speech_prob,
    )


def _make_mock_info(
    language: str = "en",
    language_probability: float = 0.99,
    duration: float = 5.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        language=language,
        language_probability=language_probability,
        duration=duration,
    )


def _build_engine_with_mock(segments, info):
    """Create a FasterWhisperBatchEngine with a mocked FasterWhisperInference."""
    engine = _WhisperEngine()

    # Mock the core's manager to return a mock model
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter(segments), info)
    engine._core._manager = MagicMock()
    engine._core._manager.acquire.return_value = mock_model
    engine._core._manager.release = MagicMock()

    return engine


class TestBatchOutputShape:
    """Verify Transcript structure from batch engine."""

    def test_output_has_text_segments_and_language(self) -> None:
        segments = [_make_mock_segment(text="hello world")]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        data = result.data
        assert data.text == "hello world"
        assert data.language == "en"
        assert data.language_confidence == 0.99
        assert data.duration == 5.0
        assert len(data.segments) == 1
        assert data.engine_id == "faster-whisper"

    def test_output_segment_has_word_timestamps(self) -> None:
        mock_words = [
            SimpleNamespace(word=" hello", start=0.0, end=0.5, probability=0.95),
            SimpleNamespace(word=" world", start=0.5, end=1.0, probability=0.92),
        ]
        segments = [_make_mock_segment(words=mock_words)]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        assert seg.words is not None
        assert len(seg.words) == 2
        assert seg.words[0].text == "hello"
        assert seg.words[0].start == 0.0
        assert seg.words[0].end == 0.5
        assert seg.words[0].confidence == 0.95

    def test_output_without_words(self) -> None:
        segments = [_make_mock_segment(words=[])]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        assert seg.words is None or seg.words == []

    def test_output_segment_metadata(self) -> None:
        segments = [
            _make_mock_segment(
                tokens=[10, 20, 30],
                avg_logprob=-0.3,
                compression_ratio=1.2,
                no_speech_prob=0.05,
            )
        ]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        assert seg.metadata["tokens"] == [10, 20, 30]
        assert seg.metadata["avg_logprob"] == -0.3
        assert seg.metadata["compression_ratio"] == 1.2
        assert seg.metadata["no_speech_prob"] == 0.05

    def test_multiple_segments(self) -> None:
        segments = [
            _make_mock_segment(start=0.0, end=2.0, text="hello world"),
            _make_mock_segment(start=2.5, end=4.0, text="good morning"),
        ]
        info = _make_mock_info(duration=4.0)
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert len(result.data.segments) == 2
        assert result.data.text == "hello world good morning"
        assert result.data.segments[0].start == 0.0
        assert result.data.segments[1].start == 2.5


class TestBatchConfigPassthrough:
    """Verify that config values are passed through to FasterWhisperInference."""

    def test_language_auto_maps_to_none(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"language": "auto"},
            ),
            _ctx(task_id, job_id),
        )

        # The core calls model.transcribe with language=None
        call_kwargs = (
            engine._core._manager.acquire.return_value.transcribe.call_args.kwargs
        )
        assert call_kwargs["language"] is None

    def test_vocabulary_passed_as_hotwords(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"vocabulary": ["Dalston", "Redis"]},
            ),
            _ctx(task_id, job_id),
        )

        call_kwargs = (
            engine._core._manager.acquire.return_value.transcribe.call_args.kwargs
        )
        assert call_kwargs["hotwords"] == "Dalston Redis"

    def test_temperature_list_passthrough(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"temperature": [0.0, 0.2, 0.4]},
            ),
            _ctx(task_id, job_id),
        )

        call_kwargs = (
            engine._core._manager.acquire.return_value.transcribe.call_args.kwargs
        )
        assert call_kwargs["temperature"] == [0.0, 0.2, 0.4]
        # Segment temperature uses first value
        assert result.data.segments[0].metadata["temperature"] == 0.0

    def test_channel_preserved_in_output(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"channel": 1},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.channel == 1

    def test_prompt_passed_as_initial_prompt(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"prompt": "This is a meeting about Dalston."},
            ),
            _ctx(task_id, job_id),
        )

        call_kwargs = (
            engine._core._manager.acquire.return_value.transcribe.call_args.kwargs
        )
        assert call_kwargs["initial_prompt"] == "This is a meeting about Dalston."

    def test_custom_beam_size(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={"beam_size": 3},
            ),
            _ctx(task_id, job_id),
        )

        call_kwargs = (
            engine._core._manager.acquire.return_value.transcribe.call_args.kwargs
        )
        assert call_kwargs["beam_size"] == 3


class TestBatchTimestampGranularity:
    """Verify timestamp granularity reporting."""

    def test_word_granularity_when_words_present(self) -> None:
        segments = [_make_mock_segment()]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.timestamp_granularity.value == "word"
        assert result.data.alignment_method.value == "attention"

    def test_segment_granularity_when_no_words(self) -> None:
        segments = [_make_mock_segment(words=[])]
        info = _make_mock_info()
        engine = _build_engine_with_mock(segments, info)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            EngineInput(
                task_id=task_id,
                job_id=job_id,
                audio_path=Path("/tmp/test.wav"),
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.timestamp_granularity.value == "segment"
        assert result.data.alignment_method.value == "attention"
