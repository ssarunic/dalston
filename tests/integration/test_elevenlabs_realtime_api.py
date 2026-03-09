"""Integration tests for ElevenLabs realtime protocol translation (M62)."""

from __future__ import annotations

import base64
import json

import pytest

from dalston.gateway.api.v1.realtime import (
    _elevenlabs_client_to_worker,
    _elevenlabs_worker_to_client,
)


class _FakeWorkerSender:
    def __init__(self) -> None:
        self.sent: list[bytes | str] = []

    async def send(self, payload: bytes | str) -> None:
        self.sent.append(payload)


class _FakeClientReceiver:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)

    async def receive(self) -> dict:
        if not self._messages:
            raise RuntimeError("No more websocket messages in fixture")
        return self._messages.pop(0)


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
async def test_elevenlabs_client_to_worker_decodes_ulaw_when_requested() -> None:
    ulaw_bytes = bytes([0x00, 0x80, 0xFF, 0x55])
    expected_pcm = b"".join(
        value.to_bytes(2, "little", signed=True) for value in [-32124, 32124, 0, -716]
    )
    client_ws = _FakeClientReceiver(
        [
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "message_type": "input_audio_chunk",
                        "audio_base_64": base64.b64encode(ulaw_bytes).decode(),
                    }
                ),
            },
            {"type": "websocket.disconnect"},
        ]
    )
    worker_ws = _FakeWorkerSender()

    await _elevenlabs_client_to_worker(
        client_ws=client_ws,
        worker_ws=worker_ws,
        session_id="sess_1",
        decode_ulaw_to_pcm16=True,
    )

    binary_payloads = [p for p in worker_ws.sent if isinstance(p, bytes)]
    assert binary_payloads
    assert binary_payloads[0] == expected_pcm


@pytest.mark.asyncio
async def test_elevenlabs_worker_to_client_formats_words_and_errors() -> None:
    worker_ws = _FakeWorkerStream(
        [
            json.dumps(
                {
                    "type": "transcript.final",
                    "text": "Hello world",
                    "language": "en",
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.4},
                        {"word": " ", "start": 0.4, "end": 0.42},
                        {"word": "world", "start": 0.42, "end": 0.9},
                    ],
                }
            ),
            json.dumps(
                {
                    "type": "error",
                    "code": "invalid_parameters",
                    "message": "Bad parameter",
                }
            ),
            json.dumps({"type": "session.end", "total_audio_seconds": 0.9}),
        ]
    )
    client_ws = _FakeClientSink()

    await _elevenlabs_worker_to_client(
        worker_ws=worker_ws,
        client_ws=client_ws,
        session_id="sess_1",
        include_timestamps=True,
    )

    assert (
        client_ws.sent_json[0]["message_type"] == "committed_transcript_with_timestamps"
    )
    assert client_ws.sent_json[0]["words"][0]["type"] == "word"
    assert client_ws.sent_json[0]["words"][1]["type"] == "spacing"

    assert client_ws.sent_json[1]["message_type"] == "error"
    assert client_ws.sent_json[1]["error"]["type"] == "server_error"
    assert client_ws.sent_json[1]["error"]["code"] == "invalid_parameters"
    assert client_ws.sent_json[2]["message_type"] == "session_ended"
