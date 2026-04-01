"""Contract tests for Riva NIM batch transcription engine (unified).

Verifies that the batch engine produces the correct Transcript
shape with segments, text, language, and word timestamps when
communicating with a mocked Riva NIM gRPC sidecar.

These tests mock the Riva client library to avoid GPU/NIM dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from dalston.engine_sdk import TaskRequest
from dalston.engine_sdk.context import BatchTaskContext

UNIFIED_RIVA_DIR = Path("engines/stt-transcribe/riva")


def _ctx(task_id: str, job_id: str) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="test-engine_id",
        instance="test-instance",
        task_id=task_id,
        job_id=job_id,
        stage="transcribe",
    )


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
    is_final: bool = True,
) -> SimpleNamespace:
    if alternatives is None:
        alternatives = [_make_mock_alternative()]
    return SimpleNamespace(
        alternatives=alternatives,
        is_final=is_final,
    )


def _make_mock_response(
    results: list | None = None,
) -> SimpleNamespace:
    if results is None:
        results = [_make_mock_result()]
    return SimpleNamespace(results=results)


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
def riva_batch_engine_class(_mock_riva_modules):
    """Load the RivaBatchEngine class with mocked riva imports."""
    # Load riva_client first (batch_engine imports from it)
    _load_module("riva_client", UNIFIED_RIVA_DIR / "riva_client.py")
    batch_mod = _load_module("batch_engine", UNIFIED_RIVA_DIR / "batch_engine.py")
    return batch_mod.RivaBatchEngine


@pytest.fixture()
def engine_with_mock(riva_batch_engine_class):
    """Create a RivaBatchEngine with a mocked RivaClient core."""
    engine = riva_batch_engine_class()
    return engine


def _setup_streaming_responses(engine, responses):
    """Configure mock to return given responses from streaming_recognize."""
    engine._core.asr.streaming_response_gen.return_value = iter(responses)


class TestRivaBatchOutputShape:
    """Verify Transcript structure from Riva batch engine."""

    def test_output_has_text_segments_and_language(
        self, engine_with_mock, tmp_path
    ) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        response = _make_mock_response()
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        data = result.data
        assert data.text == "hello world"
        assert data.language == "en"
        assert len(data.segments) == 1
        assert data.engine_id == "riva"

    def test_output_segment_has_word_timestamps(
        self, engine_with_mock, tmp_path
    ) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        words = [
            _make_mock_word("hello", 0.0, 0.5, 0.95),
            _make_mock_word("world", 0.5, 1.0, 0.92),
        ]
        response = _make_mock_response(
            results=[
                _make_mock_result(alternatives=[_make_mock_alternative(words=words)])
            ]
        )
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        assert seg.words is not None
        assert len(seg.words) == 2
        assert seg.words[0].text == "hello"
        assert seg.words[0].start == 0.0
        assert seg.words[0].end == 0.5
        assert seg.words[1].text == "world"

    def test_multiple_segments(self, engine_with_mock, tmp_path) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 32000)

        responses = [
            _make_mock_response(
                results=[
                    _make_mock_result(
                        alternatives=[
                            _make_mock_alternative(
                                transcript="hello world",
                                words=[
                                    _make_mock_word("hello", 0.0, 0.5),
                                    _make_mock_word("world", 0.5, 1.0),
                                ],
                            )
                        ]
                    )
                ]
            ),
            _make_mock_response(
                results=[
                    _make_mock_result(
                        alternatives=[
                            _make_mock_alternative(
                                transcript="good morning",
                                words=[
                                    _make_mock_word("good", 2.0, 2.5),
                                    _make_mock_word("morning", 2.5, 3.0),
                                ],
                            )
                        ]
                    )
                ]
            ),
        ]
        _setup_streaming_responses(engine, responses)

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        assert len(result.data.segments) == 2
        assert result.data.text == "hello world good morning"
        assert result.data.segments[0].start == 0.0
        assert result.data.segments[1].start == 2.0

    def test_interim_results_are_ignored(self, engine_with_mock, tmp_path) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        response = _make_mock_response(
            results=[
                _make_mock_result(is_final=False),  # interim — should be ignored
                _make_mock_result(
                    is_final=True,
                    alternatives=[_make_mock_alternative(transcript="hello world")],
                ),
            ]
        )
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        assert len(result.data.segments) == 1
        assert result.data.text == "hello world"

    def test_empty_transcript_is_skipped(self, engine_with_mock, tmp_path) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        response = _make_mock_response(
            results=[
                _make_mock_result(
                    alternatives=[_make_mock_alternative(transcript="  ")]
                ),
            ]
        )
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        assert len(result.data.segments) == 0
        assert result.data.text == ""


class TestRivaBatchConfig:
    """Verify configuration handling."""

    def test_default_language_is_english(self, engine_with_mock, tmp_path) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        response = _make_mock_response()
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.language == "en"

    def test_duration_calculated_from_audio_bytes(
        self, engine_with_mock, tmp_path
    ) -> None:
        engine = engine_with_mock

        # 2 seconds of int16 mono 16kHz audio = 2 * 16000 * 2 bytes
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 32000)

        response = _make_mock_response()
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.duration == pytest.approx(2.0)

    def test_timestamp_granularity_is_word(self, engine_with_mock, tmp_path) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        response = _make_mock_response()
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.timestamp_granularity.value == "word"


class TestRivaBatchHealthCheck:
    """Verify health check behavior."""

    def test_healthy_when_nim_reachable(self, engine_with_mock) -> None:
        engine = engine_with_mock
        engine._core.asr.stub.GetRivaSpeechRecognitionConfig.return_value = MagicMock()

        health = engine.health_check()
        assert health["status"] == "healthy"
        assert health["nim"] == "connected"

    def test_unhealthy_when_nim_unreachable(self, engine_with_mock) -> None:
        engine = engine_with_mock

        import grpc as mock_grpc

        engine._core.asr.stub.GetRivaSpeechRecognitionConfig.side_effect = (
            mock_grpc.RpcError()
        )

        health = engine.health_check()
        assert health["status"] == "unhealthy"
        assert health["nim"] == "unreachable"


class TestRivaBatchShutdown:
    """Verify shutdown behavior."""

    def test_shutdown_does_not_raise(self, engine_with_mock) -> None:
        engine = engine_with_mock
        engine.shutdown()

    def test_engine_id(self, engine_with_mock) -> None:
        engine = engine_with_mock
        assert engine.engine_id == "riva"


class TestRivaBatchWordConfidence:
    """Verify per-word confidence from Riva response."""

    def test_word_confidence_uses_per_word_value(
        self, engine_with_mock, tmp_path
    ) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        words = [
            _make_mock_word("hello", 0.0, 0.5, 0.99),
            _make_mock_word("world", 0.5, 1.0, 0.85),
        ]
        alt = _make_mock_alternative(confidence=0.90, words=words)
        response = _make_mock_response(results=[_make_mock_result(alternatives=[alt])])
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        seg = result.data.segments[0]
        # Word confidence should come from the word, not the alternative
        assert seg.words[0].confidence == 0.99
        assert seg.words[1].confidence == 0.85

    def test_timestamp_granularity_segment_when_no_words(
        self, engine_with_mock, tmp_path
    ) -> None:
        engine = engine_with_mock

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00\x00" * 16000)

        alt = _make_mock_alternative(transcript="hello world", words=[])
        response = _make_mock_response(results=[_make_mock_result(alternatives=[alt])])
        _setup_streaming_responses(engine, [response])

        task_id = str(uuid4())
        job_id = str(uuid4())
        result = engine.process(
            TaskRequest(
                task_id=task_id,
                job_id=job_id,
                audio_path=audio_file,
                config={"language": "en"},
            ),
            _ctx(task_id, job_id),
        )

        assert result.data.timestamp_granularity.value == "segment"
