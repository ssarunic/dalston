"""Unit tests for Riva NIM batch engine gRPC result mapping.

These tests verify the mapping from Riva gRPC responses to Dalston's
TranscribeOutput without requiring a live Riva NIM container.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dalston.common.pipeline_types import (
    AlignmentMethod,
    TimestampGranularity,
    TranscribeOutput,
)
from dalston.engine_sdk.context import BatchTaskContext


def _make_ctx(**overrides) -> BatchTaskContext:
    defaults = {
        "runtime": "riva",
        "instance": "test-riva-1",
        "task_id": "task-001",
        "job_id": "job-001",
        "stage": "transcribe",
        "metadata": {"language": "en"},
    }
    defaults.update(overrides)
    return BatchTaskContext(**defaults)


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
def riva_engine():
    """Create a RivaEngine with mocked gRPC client."""
    # Mock the riva.client module
    mock_riva = MagicMock()
    mock_auth = MagicMock()
    mock_asr = MagicMock()
    mock_riva.Auth.return_value = mock_auth
    mock_riva.ASRService.return_value = mock_asr

    with patch.dict(sys.modules, {"riva": MagicMock(), "riva.client": mock_riva}):
        with patch.dict("os.environ", {"RIVA_GRPC_URL": "localhost:50051"}):
            # Import after mocking

            # We need to load the engine module fresh with mocked riva
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "riva_engine",
                "engines/stt-transcribe/riva/engine.py",
            )
            module = importlib.util.module_from_spec(spec)
            # Inject mocked riva.client into the module's namespace
            module.riva = MagicMock()
            module.riva.client = mock_riva
            spec.loader.exec_module(module)

            engine = module.RivaEngine()
            engine._asr = mock_asr
            yield engine

    # Clean up injected module
    sys.modules.pop("riva_engine", None)


class TestRivaBuildOutput:
    """Test _build_output mapping from Riva response to TranscribeOutput."""

    def test_single_result_single_segment(self, riva_engine):
        words = [
            _make_word("Hello", 0.0, 0.3),
            _make_word("world", 0.4, 0.7),
        ]
        response = _make_response(
            [_make_result([_make_alternative("Hello world", words)])]
        )

        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        assert isinstance(output.data, TranscribeOutput)
        payload = output.data
        assert payload.text == "Hello world"
        assert len(payload.segments) == 1
        assert payload.segments[0].text == "Hello world"
        assert payload.segments[0].start == 0.0
        assert payload.segments[0].end == 0.7
        assert len(payload.segments[0].words) == 2
        assert payload.segments[0].words[0].text == "Hello"
        assert payload.segments[0].words[1].text == "world"

    def test_multi_result_multi_segment(self, riva_engine):
        words1 = [_make_word("First", 0.0, 0.3), _make_word("sentence", 0.4, 0.8)]
        words2 = [_make_word("Second", 1.0, 1.3), _make_word("one", 1.4, 1.6)]
        response = _make_response(
            [
                _make_result([_make_alternative("First sentence", words1)]),
                _make_result([_make_alternative("Second one", words2)]),
            ]
        )

        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        payload = output.data
        assert payload.text == "First sentence Second one"
        assert len(payload.segments) == 2
        assert payload.segments[0].text == "First sentence"
        assert payload.segments[1].text == "Second one"

    def test_empty_response(self, riva_engine):
        response = _make_response([])
        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        payload = output.data
        assert payload.text == ""
        assert len(payload.segments) == 0

    def test_result_with_no_alternatives(self, riva_engine):
        response = _make_response([_make_result([])])
        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        payload = output.data
        assert payload.text == ""
        assert len(payload.segments) == 0

    def test_alignment_method_is_native(self, riva_engine):
        words = [_make_word("test", 0.0, 0.3)]
        response = _make_response([_make_result([_make_alternative("test", words)])])

        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        assert output.data.alignment_method == AlignmentMethod.CTC

    def test_timestamp_granularity_is_word(self, riva_engine):
        words = [_make_word("test", 0.0, 0.3)]
        response = _make_response([_make_result([_make_alternative("test", words)])])

        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        assert output.data.timestamp_granularity_requested == TimestampGranularity.WORD
        assert output.data.timestamp_granularity_actual == TimestampGranularity.WORD

    def test_confidence_mapping(self, riva_engine):
        words = [_make_word("hi", 0.0, 0.2, confidence=0.88)]
        response = _make_response(
            [_make_result([_make_alternative("hi", words, confidence=0.92)])]
        )

        ctx = _make_ctx()
        output = riva_engine._build_output(response, ctx)

        assert output.data.segments[0].confidence == 0.92
        assert output.data.segments[0].words[0].confidence == 0.88

    def test_language_normalization(self, riva_engine):
        """Riva uses en-US, Dalston uses en."""
        words = [_make_word("test", 0.0, 0.3)]
        response = _make_response([_make_result([_make_alternative("test", words)])])

        ctx = _make_ctx(metadata={"language": "en-US"})
        output = riva_engine._build_output(response, ctx)

        assert output.data.language == "en"

    def test_runtime_set_from_context(self, riva_engine):
        words = [_make_word("test", 0.0, 0.3)]
        response = _make_response([_make_result([_make_alternative("test", words)])])

        ctx = _make_ctx(runtime="riva")
        output = riva_engine._build_output(response, ctx)

        assert output.data.runtime == "riva"
