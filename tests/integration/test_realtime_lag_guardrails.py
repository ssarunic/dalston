"""Integration tests for realtime lag warning/error protocol translations."""

from __future__ import annotations

import json

import pytest

from dalston.gateway.api.v1.realtime import _elevenlabs_worker_to_client


class _FakeWorkerStream:
    def __init__(self, messages: list[str | bytes]) -> None:
        self._messages = iter(messages)

    def __aiter__(self) -> _FakeWorkerStream:
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeClientSink:
    def __init__(self) -> None:
        self.sent_json: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent_json.append(payload)


@pytest.mark.asyncio
async def test_elevenlabs_worker_to_client_translates_lag_warning_and_errors():
    """Dalston lag warning/error events are translated to ElevenLabs envelopes."""
    worker_ws = _FakeWorkerStream(
        [
            json.dumps(
                {
                    "type": "warning",
                    "code": "processing_lag",
                    "message": "Processing lag is above threshold",
                    "lag_seconds": 3.7,
                    "warning_threshold_seconds": 3.0,
                    "hard_threshold_seconds": 5.0,
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "code": "lag_exceeded",
                    "message": "Realtime lag budget exceeded",
                    "recoverable": False,
                }
            ),
            json.dumps(
                {
                    "type": "session.terminated",
                    "session_id": "sess_123",
                    "reason": "lag_exceeded",
                    "recoverable": False,
                }
            ),
            json.dumps({"type": "session.end", "total_audio_seconds": 1.2}),
        ]
    )
    client_ws = _FakeClientSink()

    await _elevenlabs_worker_to_client(
        worker_ws=worker_ws,
        client_ws=client_ws,
        session_id="sess_123",
        include_timestamps=False,
    )

    assert client_ws.sent_json[0] == {
        "message_type": "warning",
        "code": "processing_lag",
        "message": "Processing lag is above threshold",
        "lag_seconds": 3.7,
        "warning_threshold_seconds": 3.0,
        "hard_threshold_seconds": 5.0,
    }
    assert client_ws.sent_json[1] == {
        "message_type": "error",
        "error": "Realtime lag budget exceeded",
    }
    assert client_ws.sent_json[2] == {
        "message_type": "error",
        "error": "lag_exceeded",
    }
    assert client_ws.sent_json[3] == {
        "message_type": "session_ended",
        "total_audio_seconds": 1.2,
    }
