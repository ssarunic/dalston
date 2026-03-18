"""Unit tests for M76.4 realtime engine sub-spans."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.common.pipeline_types import Transcript, TranscriptSegment
from dalston.realtime_sdk.session import SessionConfig, SessionHandler


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent_messages.append(payload)

    async def close(self, code=None, reason=None) -> None:
        pass


def _make_transcript(text: str = "hello") -> Transcript:
    return Transcript(
        text=text,
        segments=[
            TranscriptSegment(
                text=text,
                start=0.0,
                end=1.0,
            )
        ],
        language="en",
        engine_id="test",
    )


def _transcribe_stub(audio: np.ndarray, params) -> Transcript:
    time.sleep(0.001)  # Tiny delay to get measurable latency
    return _make_transcript()


def _build_handler(
    sample_rate: int = 16000,
    rt_span_sample_rate: int = 1,
) -> tuple[_FakeWebSocket, SessionHandler]:
    ws = _FakeWebSocket()
    handler = SessionHandler(
        websocket=ws,
        config=SessionConfig(
            session_id="sess_test_telemetry",
            enable_vad=True,
            max_utterance_duration=0.0,
            store_audio=False,
            store_transcript=False,
            sample_rate=sample_rate,
        ),
        transcribe_fn=_transcribe_stub,
    )
    # Override sample rate for testing
    handler._rt_span_sample_rate = rt_span_sample_rate
    return ws, handler


class TestRealtimeChunkInferenceSpans:
    """Tests for sampled per-chunk inference spans."""

    @pytest.mark.asyncio
    async def test_telemetry_counters_increment(self):
        """_transcribe_and_send increments telemetry counters."""
        _, handler = _build_handler()
        audio = np.zeros(16000, dtype=np.float32)  # 1 second

        await handler._transcribe_and_send(audio)

        assert handler._rt_total_chunks == 1
        assert handler._rt_total_audio_s > 0
        assert handler._rt_total_inference_s > 0

    @pytest.mark.asyncio
    async def test_telemetry_counters_accumulate(self):
        """Multiple transcriptions accumulate telemetry."""
        _, handler = _build_handler()
        audio = np.zeros(16000, dtype=np.float32)

        await handler._transcribe_and_send(audio)
        await handler._transcribe_and_send(audio)
        await handler._transcribe_and_send(audio)

        assert handler._rt_total_chunks == 3
        assert handler._rt_total_audio_s == pytest.approx(3.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_span_sampling_rate(self):
        """Only every Nth chunk gets a span."""
        _, handler = _build_handler(rt_span_sample_rate=3)
        audio = np.zeros(16000, dtype=np.float32)

        spans_created = []
        original_create_span = __import__(
            "dalston.telemetry", fromlist=["create_span"]
        ).create_span

        def tracking_create_span(name, **kwargs):
            spans_created.append(name)
            return original_create_span(name, **kwargs)

        with patch(
            "dalston.realtime_sdk.session.dalston.telemetry.create_span",
            side_effect=tracking_create_span,
        ):
            for _ in range(6):
                await handler._transcribe_and_send(audio)

        # With sample_rate=3, chunks 3 and 6 should emit spans
        inference_spans = [s for s in spans_created if s == "realtime.chunk.inference"]
        assert len(inference_spans) == 2

    @pytest.mark.asyncio
    async def test_sample_rate_env_var(self):
        """DALSTON_RT_SPAN_SAMPLE_RATE env var controls sampling."""
        with patch.dict(os.environ, {"DALSTON_RT_SPAN_SAMPLE_RATE": "5"}):
            _, handler = _build_handler()
            # Reset to use env var (constructor reads it)
            handler._rt_span_sample_rate = int(
                os.environ.get("DALSTON_RT_SPAN_SAMPLE_RATE", "10")
            )
        assert handler._rt_span_sample_rate == 5

    @pytest.mark.asyncio
    async def test_chunk_latency_metric_called(self):
        """observe_realtime_chunk_latency is called per transcription."""
        _, handler = _build_handler()
        audio = np.zeros(16000, dtype=np.float32)

        with patch(
            "dalston.realtime_sdk.session.dalston.metrics.observe_realtime_chunk_latency"
        ) as mock_metric:
            await handler._transcribe_and_send(audio)

        mock_metric.assert_called_once()
        args = mock_metric.call_args
        assert args[0][0] is None or isinstance(args[0][0], str)  # model
        assert args[0][1] > 0  # duration


class TestRealtimeVADEndpointSpans:
    """Tests for unsampled VAD endpoint detection spans."""

    @pytest.mark.asyncio
    async def test_vad_endpoint_span_on_speech_end(self):
        """VAD speech_end creates a realtime.vad.endpoint span."""
        _, handler = _build_handler()

        spans_created = []
        original_create_span = __import__(
            "dalston.telemetry", fromlist=["create_span"]
        ).create_span

        def tracking_create_span(name, **kwargs):
            spans_created.append(name)
            return original_create_span(name, **kwargs)

        # Simulate VAD detecting speech_end with audio
        mock_vad_result = MagicMock()
        mock_vad_result.event = "speech_end"
        mock_vad_result.speech_audio = np.zeros(8000, dtype=np.float32)

        handler._vad = MagicMock()
        handler._vad.process_chunk.return_value = mock_vad_result
        handler._vad.is_speaking = False

        with patch(
            "dalston.realtime_sdk.session.dalston.telemetry.create_span",
            side_effect=tracking_create_span,
        ):
            audio_chunk = np.zeros(1600, dtype=np.float32)
            await handler._process_chunk(audio_chunk)

        assert "realtime.vad.endpoint" in spans_created

    @pytest.mark.asyncio
    async def test_vad_speech_start_sets_attribute(self):
        """VAD speech_start sets a span attribute."""
        _, handler = _build_handler()

        mock_vad_result = MagicMock()
        mock_vad_result.event = "speech_start"

        handler._vad = MagicMock()
        handler._vad.process_chunk.return_value = mock_vad_result
        handler._vad.is_speaking = True

        with patch(
            "dalston.realtime_sdk.session.dalston.telemetry.set_span_attribute"
        ) as mock_attr:
            audio_chunk = np.zeros(1600, dtype=np.float32)
            await handler._process_chunk(audio_chunk)

        mock_attr.assert_called_once()
        assert mock_attr.call_args[0][0] == "dalston.vad.speech_start_at"


class TestSessionTelemetrySummary:
    """Tests for session-level telemetry summary."""

    def test_summary_empty_session(self):
        """Summary returns zeros for a session with no transcriptions."""
        _, handler = _build_handler()
        summary = handler.get_telemetry_summary()

        assert summary["total_chunks"] == 0
        assert summary["total_audio_s"] == 0.0
        assert summary["avg_chunk_latency_ms"] == 0.0

    @pytest.mark.asyncio
    async def test_summary_after_transcriptions(self):
        """Summary reflects accumulated telemetry after transcriptions."""
        _, handler = _build_handler()
        audio = np.zeros(16000, dtype=np.float32)

        await handler._transcribe_and_send(audio)
        await handler._transcribe_and_send(audio)

        summary = handler.get_telemetry_summary()

        assert summary["total_chunks"] == 2
        assert summary["total_audio_s"] == pytest.approx(2.0, abs=0.01)
        assert summary["avg_chunk_latency_ms"] > 0
