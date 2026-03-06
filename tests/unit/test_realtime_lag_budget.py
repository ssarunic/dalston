"""Unit tests for realtime lag budget enforcement in SessionHandler (M53)."""

from __future__ import annotations

import json

import pytest

from dalston.common.ws_close_codes import WS_CLOSE_LAG_EXCEEDED
from dalston.realtime_sdk.assembler import TranscribeResult
from dalston.realtime_sdk.session import SessionConfig, SessionHandler


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.closed: list[tuple[int | None, str | None]] = []

    async def send(self, payload: str) -> None:
        self.sent_messages.append(payload)

    async def close(
        self,
        code: int | None = None,
        reason: str | None = None,
    ) -> None:
        self.closed.append((code, reason))


class _ScriptedWebSocket(_FakeWebSocket):
    def __init__(self, messages: list[str]) -> None:
        super().__init__()
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def _transcribe_stub(*_args, **_kwargs) -> TranscribeResult:
    return TranscribeResult(
        text="stub",
        words=[],
        language="en",
        confidence=1.0,
    )


def _build_handler(
    lag_warning_seconds: float = 3.0,
    lag_hard_seconds: float = 5.0,
    lag_hard_grace_seconds: float = 2.0,
    encoding: str = "pcm_s16le",
    sample_rate: int = 16000,
    channels: int = 1,
) -> tuple[_FakeWebSocket, SessionHandler]:
    ws = _FakeWebSocket()
    handler = SessionHandler(
        websocket=ws,
        config=SessionConfig(
            session_id="sess_test",
            enable_vad=False,
            max_utterance_duration=0.0,
            store_audio=False,
            store_transcript=False,
            lag_warning_seconds=lag_warning_seconds,
            lag_hard_seconds=lag_hard_seconds,
            lag_hard_grace_seconds=lag_hard_grace_seconds,
            encoding=encoding,
            sample_rate=sample_rate,
            channels=channels,
        ),
        transcribe_fn=_transcribe_stub,
    )
    return ws, handler


def _decoded_messages(ws: _FakeWebSocket) -> list[dict]:
    return [json.loads(payload) for payload in ws.sent_messages]


@pytest.mark.asyncio
async def test_processing_lag_warning_is_rate_limited():
    ws, handler = _build_handler(
        lag_warning_seconds=1.0,
        lag_hard_seconds=10.0,
        lag_hard_grace_seconds=5.0,
    )

    handler._received_audio_seconds = 4.0
    handler._processed_audio_seconds = 0.0

    await handler._evaluate_lag_budget(source="test", now=10.0)
    await handler._evaluate_lag_budget(source="test", now=10.4)
    await handler._evaluate_lag_budget(source="test", now=11.1)

    warnings = [msg for msg in _decoded_messages(ws) if msg.get("type") == "warning"]
    assert len(warnings) == 2
    assert warnings[0]["code"] == "processing_lag"
    assert warnings[0]["warning_threshold_seconds"] == 1.0
    assert warnings[0]["hard_threshold_seconds"] == 10.0


@pytest.mark.asyncio
async def test_hard_lag_grace_timer_resets_when_lag_drops():
    ws, handler = _build_handler(
        lag_warning_seconds=1.0,
        lag_hard_seconds=2.0,
        lag_hard_grace_seconds=2.0,
    )

    handler._received_audio_seconds = 5.0
    handler._processed_audio_seconds = 1.0  # lag=4.0 (above hard)
    await handler._evaluate_lag_budget(source="test", now=0.0)
    assert handler._lag_hard_exceeded_since == 0.0

    # Drop below hard threshold: grace timer must reset.
    handler._processed_audio_seconds = 3.2  # lag=1.8 (below hard=2.0)
    await handler._evaluate_lag_budget(source="test", now=0.8)
    assert handler._lag_hard_exceeded_since is None

    # Cross hard threshold again and hold beyond grace window.
    handler._processed_audio_seconds = 1.0  # lag=4.0
    await handler._evaluate_lag_budget(source="test", now=1.0)
    await handler._evaluate_lag_budget(source="test", now=2.9)
    assert ws.closed == []
    await handler._evaluate_lag_budget(source="test", now=3.1)

    assert ws.closed == [(WS_CLOSE_LAG_EXCEEDED, "Realtime lag budget exceeded")]
    messages = _decoded_messages(ws)
    assert any(
        msg.get("type") == "error" and msg.get("code") == "lag_exceeded"
        for msg in messages
    )
    assert any(
        msg.get("type") == "session.terminated"
        and msg.get("reason") == "lag_exceeded"
        and msg.get("recoverable") is False
        and "recovery_hint" not in msg
        for msg in messages
    )
    assert handler.error == "lag_exceeded"


def test_audio_bytes_to_seconds_respects_encoding_sample_rate_and_channels():
    _, pcm_handler = _build_handler(
        encoding="pcm_f32le",
        sample_rate=8000,
        channels=2,
    )
    assert pcm_handler._audio_bytes_to_seconds(64000) == pytest.approx(1.0)

    _, mulaw_handler = _build_handler(
        encoding="mulaw",
        sample_rate=8000,
        channels=1,
    )
    assert mulaw_handler._audio_bytes_to_seconds(8000) == pytest.approx(1.0)


def test_discarded_audio_reduces_effective_lag():
    _, handler = _build_handler()
    handler._received_audio_seconds = 3.0
    handler._processed_audio_seconds = 1.0
    handler._record_discarded_audio(2.0)

    assert handler._lag_seconds() == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_debug_chunk_sleep_is_progressive(monkeypatch):
    _, handler = _build_handler()
    handler.config.debug_chunk_sleep_initial_seconds = 0.1
    handler.config.debug_chunk_sleep_increment_seconds = 0.05
    handler._next_debug_chunk_sleep_seconds = 0.1

    sleep_calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("dalston.realtime_sdk.session.asyncio.sleep", _fake_sleep)

    await handler._maybe_apply_debug_chunk_sleep()
    await handler._maybe_apply_debug_chunk_sleep()
    await handler._maybe_apply_debug_chunk_sleep()

    assert sleep_calls == pytest.approx([0.1, 0.15, 0.2])
    assert handler._next_debug_chunk_sleep_seconds == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_run_does_not_double_cleanup_after_end_message():
    ws = _ScriptedWebSocket(messages=['{"type":"end"}'])
    _, handler = _build_handler()
    handler.websocket = ws

    cleanup_calls = 0
    original_cleanup = handler._cleanup_storage

    async def _wrapped_cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        await original_cleanup()

    handler._cleanup_storage = _wrapped_cleanup  # type: ignore[method-assign]

    await handler.run()

    assert cleanup_calls == 1
    message_types = [msg.get("type") for msg in _decoded_messages(ws)]
    assert "session.begin" in message_types
    assert "session.end" in message_types
