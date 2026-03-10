"""Unit tests for RealtimeProxy service (M65).

Tests cover:
- Successful session lifecycle (allocate → connect → release → finalize)
- No-capacity path (on_no_capacity callback + close)
- RealtimeLagExceededError → status=error, error=lag_exceeded
- WebSocketDisconnect → status=interrupted
- Generic exception → status=error, error=str(exc)
- session_params=None skips DB operations
- DB create failure does not abort the session
- Keepalive task is cancelled on exit
- Rate-limit decrement is NOT called by run() (caller's responsibility)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from starlette.websockets import WebSocketDisconnect

from dalston.gateway.api.v1._realtime_common import RealtimeLagExceededError
from dalston.gateway.services.realtime_proxy import ProxySessionParams, RealtimeProxy

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
KEY_ID = UUID("00000000-0000-0000-0000-000000000002")


_NO_ALLOCATION = object()  # sentinel: "caller wants acquire_worker to return None"


@dataclass
class _FakeAllocation:
    instance: str = "worker-1"
    endpoint: str = "ws://worker-1:9000"
    session_id: str = "sess_abc123"
    runtime: str = "faster-whisper"


class _FakeWebSocket:
    def __init__(self) -> None:
        self.client = MagicMock(host="127.0.0.1")
        self.closed_code: int | None = None
        self.closed_reason: str | None = None
        self.sent_json: list[dict] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code
        self.closed_reason = reason

    async def send_json(self, payload: dict) -> None:
        self.sent_json.append(payload)


class _FakeRoutingParams:
    routing_model: str | None = "faster-whisper-large-v3"
    model_runtime: str | None = "faster-whisper"
    valid_runtimes: set[str] | None = {"faster-whisper"}
    effective_model: str = "faster-whisper-large-v3"


def _make_session_params() -> ProxySessionParams:
    return ProxySessionParams(
        tenant_id=TENANT_ID,
        client_ip="127.0.0.1",
        language="en",
        model="faster-whisper-large-v3",
        created_by_key_id=KEY_ID,
        encoding="pcm_s16le",
        sample_rate=16000,
        retention=30,
    )


def _make_session_router(
    allocation: _FakeAllocation | object = _NO_ALLOCATION,
) -> AsyncMock:
    """Build a mock session router.

    Pass ``allocation=None`` to simulate no capacity (acquire_worker returns None).
    Omit the argument to use a default _FakeAllocation.
    """
    router = AsyncMock()
    router.acquire_worker.return_value = (
        _FakeAllocation() if allocation is _NO_ALLOCATION else allocation
    )
    router.release_worker.return_value = None
    router.extend_session_ttl.return_value = None
    return router


# ---------------------------------------------------------------------------
# RealtimeProxy.run() – happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_calls_connect_and_releases_worker() -> None:
    """run() calls the connect callback and releases the worker on exit."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(
        return_value={"total_audio_seconds": 5, "segments": [], "transcript": ""}
    )

    with (
        patch("dalston.gateway.services.realtime_proxy._get_db"),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
        ) as mock_svc_cls,
        patch("dalston.gateway.services.realtime_proxy.get_settings") as mock_settings,
    ):
        mock_settings.return_value.retention_default_days = 30
        mock_svc = AsyncMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_session = AsyncMock()
        mock_svc.update_stats = AsyncMock()
        mock_svc.finalize_session = AsyncMock()

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=_make_session_params(),
        )

    connect.assert_called_once()
    session_router.release_worker.assert_called_once_with("sess_abc123")


@pytest.mark.asyncio
async def test_run_finalises_db_session_with_stats() -> None:
    """run() calls update_stats and finalize_session with data from session.end."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    session_end_data = {
        "total_audio_seconds": 12.5,
        "segments": [{"text": "hi"}, {"text": "there"}],
        "transcript": "hi there",
        "audio_uri": "s3://bucket/audio.wav",
        "transcript_uri": "s3://bucket/transcript.json",
    }
    connect = AsyncMock(return_value=session_end_data)

    with (
        patch("dalston.gateway.services.realtime_proxy._get_db"),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
        ) as mock_svc_cls,
        patch("dalston.gateway.services.realtime_proxy.get_settings"),
    ):
        mock_svc = AsyncMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_session = AsyncMock()
        mock_svc.update_stats = AsyncMock()
        mock_svc.finalize_session = AsyncMock()

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=_make_session_params(),
        )

    mock_svc.update_stats.assert_called_once_with(
        session_id="sess_abc123",
        audio_duration_seconds=12.5,
        segment_count=2,
        word_count=2,
    )
    mock_svc.finalize_session.assert_called_once_with(
        session_id="sess_abc123",
        status="completed",
        error=None,
        audio_uri="s3://bucket/audio.wav",
        transcript_uri="s3://bucket/transcript.json",
    )


# ---------------------------------------------------------------------------
# No-capacity path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_calls_on_no_capacity_and_closes() -> None:
    """When no worker is available, on_no_capacity is called and WS is closed."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router(allocation=None)
    on_no_capacity = AsyncMock()
    connect = AsyncMock()

    proxy = RealtimeProxy()
    await proxy.run(
        websocket=websocket,
        session_router=session_router,
        routing_params=_FakeRoutingParams(),
        language="en",
        connect=connect,
        on_no_capacity=on_no_capacity,
    )

    on_no_capacity.assert_called_once()
    assert websocket.closed_code is not None
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_run_no_capacity_without_callback_still_closes() -> None:
    """WS is closed even when no on_no_capacity callback is provided."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router(allocation=None)
    connect = AsyncMock()

    proxy = RealtimeProxy()
    await proxy.run(
        websocket=websocket,
        session_router=session_router,
        routing_params=_FakeRoutingParams(),
        language="en",
        connect=connect,
    )

    assert websocket.closed_code is not None
    connect.assert_not_called()


# ---------------------------------------------------------------------------
# Error / disconnect handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_lag_exceeded_marks_session_error() -> None:
    """RealtimeLagExceededError → finalize_session called with status=error."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(side_effect=RealtimeLagExceededError("lag_exceeded"))

    with (
        patch("dalston.gateway.services.realtime_proxy._get_db"),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
        ) as mock_svc_cls,
        patch("dalston.gateway.services.realtime_proxy.get_settings"),
    ):
        mock_svc = AsyncMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_session = AsyncMock()
        mock_svc.update_stats = AsyncMock()
        mock_svc.finalize_session = AsyncMock()

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=_make_session_params(),
        )

    mock_svc.finalize_session.assert_called_once()
    _, kwargs = mock_svc.finalize_session.call_args
    assert kwargs["status"] == "error"
    assert kwargs["error"] == "lag_exceeded"


@pytest.mark.asyncio
async def test_run_websocket_disconnect_marks_interrupted() -> None:
    """WebSocketDisconnect → finalize_session called with status=interrupted."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(side_effect=WebSocketDisconnect(code=1000))

    with (
        patch("dalston.gateway.services.realtime_proxy._get_db"),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
        ) as mock_svc_cls,
        patch("dalston.gateway.services.realtime_proxy.get_settings"),
    ):
        mock_svc = AsyncMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_session = AsyncMock()
        mock_svc.update_stats = AsyncMock()
        mock_svc.finalize_session = AsyncMock()

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=_make_session_params(),
        )

    mock_svc.finalize_session.assert_called_once()
    _, kwargs = mock_svc.finalize_session.call_args
    assert kwargs["status"] == "interrupted"


@pytest.mark.asyncio
async def test_run_generic_exception_marks_error() -> None:
    """Any unhandled exception from connect → finalize_session with status=error."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(side_effect=RuntimeError("something went wrong"))

    with (
        patch("dalston.gateway.services.realtime_proxy._get_db"),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
        ) as mock_svc_cls,
        patch("dalston.gateway.services.realtime_proxy.get_settings"),
    ):
        mock_svc = AsyncMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_session = AsyncMock()
        mock_svc.update_stats = AsyncMock()
        mock_svc.finalize_session = AsyncMock()

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=_make_session_params(),
        )

    mock_svc.finalize_session.assert_called_once()
    _, kwargs = mock_svc.finalize_session.call_args
    assert kwargs["status"] == "error"
    assert "something went wrong" in kwargs["error"]


# ---------------------------------------------------------------------------
# No session_params → DB operations skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_without_session_params_skips_db() -> None:
    """When session_params=None, no DB operations are performed."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(return_value=None)

    with patch(
        "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
    ) as mock_svc_cls:
        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=None,
        )

    mock_svc_cls.assert_not_called()
    session_router.release_worker.assert_called_once_with("sess_abc123")


# ---------------------------------------------------------------------------
# DB create failure does not abort session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_db_create_failure_still_runs_session() -> None:
    """A DB create failure is logged but the session continues via Redis."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(return_value=None)

    with (
        patch("dalston.gateway.services.realtime_proxy._get_db"),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService"
        ) as mock_svc_cls,
        patch("dalston.gateway.services.realtime_proxy.get_settings"),
    ):
        mock_svc = AsyncMock()
        mock_svc_cls.return_value = mock_svc
        # Simulate DB create failure
        mock_svc.create_session = AsyncMock(side_effect=Exception("DB unavailable"))

        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect,
            session_params=_make_session_params(),
        )

    # connect should still be called even if DB create failed
    connect.assert_called_once()
    session_router.release_worker.assert_called_once_with("sess_abc123")


# ---------------------------------------------------------------------------
# Keepalive task lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_keepalive_cancelled_after_session() -> None:
    """The keepalive task is created and cancelled when the session ends."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    keepalive_started = asyncio.Event()
    keepalive_cancelled = asyncio.Event()

    async def _long_keepalive(*args, **kwargs):
        keepalive_started.set()
        try:
            await asyncio.sleep(9999)
        except asyncio.CancelledError:
            keepalive_cancelled.set()
            raise

    # connect yields to the event loop so the keepalive task gets to start
    async def connect_with_yield(ws, alloc):
        await asyncio.sleep(0)  # give keepalive task a turn
        return None

    with patch(
        "dalston.gateway.services.realtime_proxy.keep_session_alive",
        new=_long_keepalive,
    ):
        proxy = RealtimeProxy()
        await proxy.run(
            websocket=websocket,
            session_router=session_router,
            routing_params=_FakeRoutingParams(),
            language="en",
            connect=connect_with_yield,
            session_params=None,
        )

    assert keepalive_started.is_set(), "Keepalive task was never started"
    assert keepalive_cancelled.is_set(), (
        "Keepalive task was not cancelled after session"
    )


# ---------------------------------------------------------------------------
# run() does NOT call decrement (caller's responsibility)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_does_not_call_decrement_session_count() -> None:
    """run() must not call decrement_realtime_session_count; that is the caller's job.

    Verified by checking the proxy module's imported names do not include
    decrement_realtime_session_count.
    """
    import dalston.gateway.services.realtime_proxy as proxy_module

    assert not hasattr(proxy_module, "decrement_realtime_session_count"), (
        "RealtimeProxy must not import decrement_realtime_session_count – that is the adapter's responsibility."
    )


# ---------------------------------------------------------------------------
# Worker release always happens (even on exception)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_releases_worker_on_connect_exception() -> None:
    """Worker is released even when the connect callback raises."""
    websocket = _FakeWebSocket()
    session_router = _make_session_router()
    connect = AsyncMock(side_effect=RuntimeError("crash"))

    proxy = RealtimeProxy()
    await proxy.run(
        websocket=websocket,
        session_router=session_router,
        routing_params=_FakeRoutingParams(),
        language="en",
        connect=connect,
        session_params=None,
    )

    session_router.release_worker.assert_called_once_with("sess_abc123")
