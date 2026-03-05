"""Phase-5 tests for realtime session storage adapters."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.realtime_sdk.session import SessionConfig, SessionHandler


class _FakeStorage:
    def __init__(self) -> None:
        self.started = False
        self.appended: list[bytes] = []

    async def start(self, session_id: str, config: SessionConfig) -> None:
        self.started = True

    async def append_audio(self, chunk: bytes) -> None:
        self.appended.append(chunk)

    async def save_transcript(self, transcript_data):
        return None

    async def finalize(self):
        from dalston.realtime_sdk.context import SessionStorageResult

        return SessionStorageResult()

    async def abort(self) -> None:
        return None


@pytest.mark.asyncio
async def test_session_storage_init_flushes_buffered_audio():
    handler = SessionHandler(
        websocket=MagicMock(),
        config=SessionConfig(
            session_id="sess-1", store_audio=True, store_transcript=True
        ),
        transcribe_fn=AsyncMock(return_value=None),
    )
    handler._raw_audio_buffer = [b"a", b"b"]
    fake_storage = _FakeStorage()

    with patch(
        "dalston.realtime_sdk.session.S3SessionStorage", return_value=fake_storage
    ):
        await handler._init_storage()

    assert fake_storage.started is True
    assert fake_storage.appended == [b"a", b"b"]
    assert handler._raw_audio_buffer == []


@pytest.mark.asyncio
async def test_session_storage_init_failure_sets_guard_flag():
    handler = SessionHandler(
        websocket=MagicMock(),
        config=SessionConfig(
            session_id="sess-2", store_audio=True, store_transcript=False
        ),
        transcribe_fn=AsyncMock(return_value=None),
    )
    handler._raw_audio_buffer = [b"payload"]

    failing_storage = MagicMock()
    failing_storage.start = AsyncMock(side_effect=RuntimeError("storage down"))

    with patch(
        "dalston.realtime_sdk.session.S3SessionStorage", return_value=failing_storage
    ):
        await handler._init_storage()

    assert handler._storage_init_failed is True
    assert handler._raw_audio_buffer == []
