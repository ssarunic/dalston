"""Integration tests for OpenAI realtime protocol translation (M38)."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dalston.common.ws_close_codes import WS_CLOSE_INVALID_REQUEST
from dalston.gateway.api.v1.openai_realtime import (
    OpenAISessionState,
    RealtimeLagExceededError,
    _openai_client_to_worker,
    _openai_worker_to_client,
    openai_realtime_router,
)
from dalston.gateway.dependencies import (
    get_db,
    get_rate_limiter,
    get_redis,
    get_security_manager,
    require_auth,
)
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.services.auth import DEFAULT_EXPIRES_AT, APIKey, Scope
from dalston.gateway.services.rate_limiter import RateLimitResult


class _FakeWorkerSender:
    def __init__(self) -> None:
        self.sent: list[bytes | str] = []

    async def send(self, payload: bytes | str) -> None:
        self.sent.append(payload)


class _FakeClientReceiver:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict] = []

    async def receive(self) -> dict:
        if not self._messages:
            raise RuntimeError("No more websocket messages in test fixture")
        return self._messages.pop(0)

    async def send_json(self, payload: dict) -> None:
        self.sent_json.append(payload)


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


def _build_api_key() -> APIKey:
    return APIKey(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        key_hash="abc123def456",
        prefix="dk_test12",
        name="Test Key",
        tenant_id=UUID("00000000-0000-0000-0000-000000000000"),
        scopes=[Scope.JOBS_READ, Scope.JOBS_WRITE, Scope.REALTIME],
        rate_limit=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        expires_at=DEFAULT_EXPIRES_AT,
        revoked_at=None,
    )


@pytest.mark.asyncio
async def test_openai_worker_to_client_translates_protocol_events():
    """Worker protocol messages are translated to OpenAI event types."""
    worker_ws = _FakeWorkerStream(
        [
            json.dumps({"type": "vad.speech_start", "timestamp": 0.25}),
            json.dumps({"type": "transcript.partial", "text": "Hello"}),
            json.dumps({"type": "transcript.final", "text": "Hello world"}),
            json.dumps({"type": "vad.speech_end", "timestamp": 1.10}),
            json.dumps({"type": "session.end", "total_audio_seconds": 1.2}),
        ]
    )
    client_ws = _FakeClientSink()
    session_state = OpenAISessionState()
    session_state.current_item_id = "item_fixed"

    session_end_data = await _openai_worker_to_client(
        worker_ws=worker_ws,
        client_ws=client_ws,
        session_id="sess_1",
        openai_session_id="sess_openai_1",
        session_state=session_state,
    )

    event_types = [payload["type"] for payload in client_ws.sent_json]
    assert event_types == [
        "input_audio_buffer.speech_started",
        "conversation.item.input_audio_transcription.delta",
        "conversation.item.input_audio_transcription.completed",
        "input_audio_buffer.speech_stopped",
    ]
    assert client_ws.sent_json[0]["audio_start_ms"] == 250
    assert client_ws.sent_json[3]["audio_end_ms"] == 1100
    assert client_ws.sent_json[1]["item_id"] == "item_fixed"
    assert client_ws.sent_json[2]["item_id"] == "item_fixed"
    assert session_state.current_item_id == "item_fixed"
    assert session_end_data is not None
    assert session_end_data["total_audio_seconds"] == 1.2


@pytest.mark.asyncio
async def test_openai_worker_to_client_translates_warning_and_lag_error():
    """Lag warning/error protocol messages are translated to OpenAI format."""
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
            json.dumps({"type": "session.end", "total_audio_seconds": 1.2}),
        ]
    )
    client_ws = _FakeClientSink()
    session_state = OpenAISessionState()
    session_state.current_item_id = "item_fixed"

    await _openai_worker_to_client(
        worker_ws=worker_ws,
        client_ws=client_ws,
        session_id="sess_1",
        openai_session_id="sess_openai_1",
        session_state=session_state,
    )

    event_types = [payload["type"] for payload in client_ws.sent_json]
    assert event_types == ["warning", "error"]

    warning_payload = client_ws.sent_json[0]["warning"]
    assert warning_payload["code"] == "processing_lag"
    assert warning_payload["lag_seconds"] == 3.7
    assert warning_payload["warning_threshold_seconds"] == 3.0
    assert warning_payload["hard_threshold_seconds"] == 5.0

    error_payload = client_ws.sent_json[1]["error"]
    assert error_payload["code"] == "lag_exceeded"
    assert error_payload["message"] == "Realtime lag budget exceeded"


@pytest.mark.asyncio
async def test_openai_client_to_worker_translates_protocol_messages():
    """OpenAI client protocol messages are translated to worker protocol."""
    audio_chunk = b"\x00\x01\x02\x03"
    client_ws = _FakeClientReceiver(
        [
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "transcription_session.update",
                        "session": {
                            "input_audio_format": "pcm16",
                            "input_audio_transcription": {
                                "model": "gpt-4o-transcribe",
                                "language": "en",
                            },
                        },
                    }
                ),
            },
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(audio_chunk).decode(),
                    }
                ),
            },
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "input_audio_buffer.commit"}),
            },
            {"type": "websocket.disconnect"},
        ]
    )
    worker_ws = _FakeWorkerSender()
    session_state = OpenAISessionState()
    session_state.current_item_id = "item_orig"
    session_config = {
        "language": "auto",
        "encoding": "pcm_s16le",
        "client_sample_rate": 24000,
        "sample_rate": 16000,
        "enable_vad": True,
        "interim_results": True,
        "word_timestamps": False,
        "vocabulary": None,
    }

    await _openai_client_to_worker(
        client_ws=client_ws,
        worker_ws=worker_ws,
        session_id="sess_1",
        session_config=session_config,
        session_state=session_state,
    )

    text_payloads = [json.loads(p) for p in worker_ws.sent if isinstance(p, str)]
    config_payload = next(p for p in text_payloads if p.get("type") == "config")
    assert config_payload["language"] == "en"
    assert "vad_threshold" in config_payload
    assert "min_silence_duration_ms" in config_payload
    assert "prefix_padding_ms" in config_payload
    assert {"type": "flush"} in text_payloads
    assert {"type": "end"} in text_payloads
    assert any(p == audio_chunk for p in worker_ws.sent if isinstance(p, bytes))

    client_event_types = [payload["type"] for payload in client_ws.sent_json]
    assert "transcription_session.updated" in client_event_types
    assert "conversation.item.created" in client_event_types
    assert "input_audio_buffer.committed" in client_event_types

    created = next(
        payload
        for payload in client_ws.sent_json
        if payload["type"] == "conversation.item.created"
    )
    committed = next(
        payload
        for payload in client_ws.sent_json
        if payload["type"] == "input_audio_buffer.committed"
    )
    assert created["item"]["id"] == "item_orig"
    assert committed["item_id"] == "item_orig"
    assert created["previous_item_id"] is None
    assert committed["previous_item_id"] is None
    assert session_state.previous_item_id == "item_orig"


@pytest.mark.asyncio
async def test_openai_client_to_worker_commit_chains_previous_item_id():
    """Second commit should reference first commit in previous_item_id."""
    client_ws = _FakeClientReceiver(
        [
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "input_audio_buffer.commit"}),
            },
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "input_audio_buffer.commit"}),
            },
            {"type": "websocket.disconnect"},
        ]
    )
    worker_ws = _FakeWorkerSender()
    session_state = OpenAISessionState()
    session_state.current_item_id = "item_seed"
    session_config = {
        "language": "auto",
        "encoding": "pcm_s16le",
        "client_sample_rate": 24000,
        "sample_rate": 16000,
        "enable_vad": True,
        "interim_results": True,
        "word_timestamps": False,
        "vocabulary": None,
    }

    await _openai_client_to_worker(
        client_ws=client_ws,
        worker_ws=worker_ws,
        session_id="sess_chain",
        session_config=session_config,
        session_state=session_state,
    )

    created_events = [
        payload
        for payload in client_ws.sent_json
        if payload["type"] == "conversation.item.created"
    ]
    committed_events = [
        payload
        for payload in client_ws.sent_json
        if payload["type"] == "input_audio_buffer.committed"
    ]
    assert len(created_events) == 2
    assert len(committed_events) == 2
    assert created_events[0]["item"]["id"] == "item_seed"
    assert created_events[0]["previous_item_id"] is None
    assert committed_events[0]["item_id"] == "item_seed"
    assert committed_events[0]["previous_item_id"] is None

    second_id = created_events[1]["item"]["id"]
    assert second_id != "item_seed"
    assert created_events[1]["previous_item_id"] == "item_seed"
    assert committed_events[1]["item_id"] == second_id
    assert committed_events[1]["previous_item_id"] == "item_seed"


@pytest.mark.asyncio
async def test_openai_client_to_worker_accepts_flat_session_update_shape():
    """Flat session.update payload should normalize and update worker config."""
    client_ws = _FakeClientReceiver(
        [
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "session.update",
                        "input_audio_format": "g711_ulaw",
                        "input_audio_transcription": {
                            "model": "gpt-4o-transcribe",
                            "language": "fr",
                            "prompt": "bonjour le monde",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.42,
                            "silence_duration_ms": 650,
                            "prefix_padding_ms": 180,
                        },
                    }
                ),
            },
            {"type": "websocket.disconnect"},
        ]
    )
    worker_ws = _FakeWorkerSender()
    session_state = OpenAISessionState()
    session_config = {
        "language": "auto",
        "encoding": "pcm_s16le",
        "client_sample_rate": 24000,
        "sample_rate": 16000,
        "enable_vad": True,
        "interim_results": True,
        "word_timestamps": False,
        "vocabulary": None,
    }

    await _openai_client_to_worker(
        client_ws=client_ws,
        worker_ws=worker_ws,
        session_id="sess_flat",
        session_config=session_config,
        session_state=session_state,
    )

    text_payloads = [json.loads(p) for p in worker_ws.sent if isinstance(p, str)]
    config_payload = next(p for p in text_payloads if p.get("type") == "config")
    assert config_payload["language"] == "fr"
    assert config_payload["vad_threshold"] == 0.42
    assert config_payload["min_silence_duration_ms"] == 650
    assert config_payload["prefix_padding_ms"] == 180

    updated_event = next(
        payload
        for payload in client_ws.sent_json
        if payload["type"] == "transcription_session.updated"
    )
    assert updated_event["session"]["input_audio_format"] == "g711_ulaw"
    assert updated_event["session"]["input_audio_transcription"]["language"] == "fr"
    assert (
        updated_event["session"]["input_audio_transcription"]["prompt"]
        == "bonjour le monde"
    )


def _build_rest_realtime_create_app(api_key: APIKey) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(openai_realtime_router, prefix="/v1")

    limiter = AsyncMock()
    limiter.check_request_rate.return_value = RateLimitResult(
        allowed=True,
        limit=100,
        remaining=99,
        reset_seconds=60,
    )

    async def _fake_require_auth() -> APIKey:
        return api_key

    async def _fake_get_db() -> AsyncMock:
        return AsyncMock()

    async def _fake_get_redis() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[require_auth] = _fake_require_auth
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_security_manager] = lambda: SecurityManager(
        mode="api_key"
    )
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_redis] = _fake_get_redis
    return app, limiter


def test_openai_realtime_session_create_endpoint_returns_client_secret():
    """REST create endpoint returns OpenAI-compatible session payload and headers."""
    api_key = _build_api_key()
    app, _ = _build_rest_realtime_create_app(api_key)

    token_expiry = datetime.now(UTC) + timedelta(minutes=10)
    created_token = SimpleNamespace(expires_at=token_expiry)

    with patch(
        "dalston.gateway.api.v1.openai_realtime.AuthService.create_session_token",
        new_callable=AsyncMock,
    ) as create_token_mock:
        create_token_mock.return_value = ("tk_test_session", created_token)
        client = TestClient(app)
        response = client.post(
            "/v1/realtime/transcription_sessions",
            headers={"Authorization": "Bearer dk_test"},
            json={
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gpt-4o-transcribe",
                    "language": "en",
                    "prompt": "test prompt",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.4,
                    "silence_duration_ms": 700,
                    "prefix_padding_ms": 200,
                },
                "input_audio_noise_reduction": {"type": "near_field"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["client_secret"]["value"] == "tk_test_session"
    assert payload["input_audio_format"] == "pcm16"
    assert payload["input_audio_transcription"]["model"] == "gpt-4o-transcribe"
    assert payload["input_audio_transcription"]["language"] == "en"
    assert payload["input_audio_transcription"]["prompt"] == "test prompt"
    assert payload["turn_detection"]["type"] == "server_vad"
    assert payload["input_audio_noise_reduction"]["type"] == "near_field"
    assert response.headers["x-ratelimit-limit-requests"] == "100"
    assert response.headers["x-ratelimit-remaining-requests"] == "99"

    create_token_mock.assert_awaited_once()
    assert create_token_mock.await_args.kwargs["api_key"] == api_key
    assert create_token_mock.await_args.kwargs["scopes"] == [Scope.REALTIME]


def test_openai_realtime_session_create_endpoint_accepts_wrapped_session_shape():
    """REST create endpoint accepts legacy wrapped {'session': ...} payload shape."""
    api_key = _build_api_key()
    app, _ = _build_rest_realtime_create_app(api_key)

    token_expiry = datetime.now(UTC) + timedelta(minutes=10)
    created_token = SimpleNamespace(expires_at=token_expiry)

    with patch(
        "dalston.gateway.api.v1.openai_realtime.AuthService.create_session_token",
        new_callable=AsyncMock,
    ) as create_token_mock:
        create_token_mock.return_value = ("tk_wrapped", created_token)
        client = TestClient(app)
        response = client.post(
            "/v1/realtime/transcription_sessions",
            headers={"Authorization": "Bearer dk_test"},
            json={
                "session": {
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {"model": "gpt-4o-transcribe"},
                }
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["client_secret"]["value"] == "tk_wrapped"
    assert payload["input_audio_transcription"]["model"] == "gpt-4o-transcribe"
    create_token_mock.assert_awaited_once()


def test_openai_realtime_session_create_endpoint_rejects_invalid_model():
    """REST create endpoint validates model against realtime capability table."""
    api_key = _build_api_key()
    app, _ = _build_rest_realtime_create_app(api_key)
    client = TestClient(app)
    response = client.post(
        "/v1/realtime/transcription_sessions",
        headers={"Authorization": "Bearer dk_test"},
        json={
            "input_audio_transcription": {
                "model": "not-a-real-openai-model",
            }
        },
    )

    assert response.status_code == 400
    payload = response.json()
    error = payload.get("error") or payload.get("detail", {}).get("error")
    assert error is not None
    assert error["param"] == "model"
    assert error["code"] in {"model_not_found", "invalid_model"}


def test_openai_realtime_endpoint_rejects_invalid_model():
    """Endpoint closes with invalid-request code for unsupported model IDs."""
    app = FastAPI()
    app.include_router(openai_realtime_router, prefix="/v1")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/v1/realtime?intent=transcription&model=invalid-model"
        ):
            pass

    assert exc_info.value.code == WS_CLOSE_INVALID_REQUEST


def test_openai_realtime_endpoint_sends_session_created_event():
    """Endpoint returns OpenAI session-created event on successful handshake."""
    app = FastAPI()
    app.include_router(openai_realtime_router, prefix="/v1")

    api_key = _build_api_key()
    session_router = AsyncMock()
    session_router.acquire_worker.return_value = SimpleNamespace(
        session_id="sess_abc123",
        endpoint="ws://worker.test",
        instance="worker-1",
        runtime="parakeet",
    )
    session_router.release_worker.return_value = None

    rt = SimpleNamespace(
        routing_model="auto",
        model_runtime="parakeet",
        valid_runtimes=["parakeet"],
        effective_model="gpt-4o-transcribe",
    )

    async def _auth_db_gen():
        if False:
            yield None

    async def _db_gen():
        yield AsyncMock()

    async def _fake_get_auth_service():
        return MagicMock(), _auth_db_gen()

    async def _fake_authenticate_websocket(*args, **kwargs):
        return api_key

    async def _fake_rate_limits(*args, **kwargs):
        return True

    async def _fake_resolve_rt_routing(model):
        assert model is None
        return rt

    async def _fake_proxy_to_worker_openai(*args, **kwargs):
        return {
            "type": "session.end",
            "total_audio_seconds": 0.0,
            "segments": [],
            "transcript": "",
            "transcript_uri": None,
        }

    async def _fake_keepalive(*args, **kwargs):
        await asyncio.sleep(3600)

    async def _fake_decrement_session_count(*args, **kwargs):
        return None

    session_service = MagicMock()
    session_service.create_session = AsyncMock()
    session_service.update_stats = AsyncMock()
    session_service.finalize_session = AsyncMock()

    with (
        patch(
            "dalston.gateway.api.v1.openai_realtime.get_session_router",
            return_value=session_router,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._get_auth_service",
            side_effect=_fake_get_auth_service,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime.authenticate_websocket",
            side_effect=_fake_authenticate_websocket,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._check_realtime_rate_limits",
            side_effect=_fake_rate_limits,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._resolve_rt_routing",
            side_effect=_fake_resolve_rt_routing,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._proxy_to_worker_openai",
            side_effect=_fake_proxy_to_worker_openai,
        ),
        patch(
            "dalston.gateway.services.realtime_proxy.keep_session_alive",
            side_effect=_fake_keepalive,
        ),
        patch(
            "dalston.gateway.services.realtime_proxy._get_db",
            side_effect=lambda: _db_gen(),
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._decrement_session_count",
            side_effect=_fake_decrement_session_count,
        ),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService",
            return_value=session_service,
        ),
    ):
        client = TestClient(app)
        with client.websocket_connect(
            "/v1/realtime?intent=transcription&model=gpt-4o-transcribe",
            headers={
                "Authorization": "Bearer dk_test",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as websocket:
            created = websocket.receive_json()
            assert created["type"] == "transcription_session.created"
            assert created["session"]["model"] == "gpt-4o-transcribe"
            assert created["session"]["id"].startswith("sess_")


def test_openai_realtime_endpoint_persists_lag_exceeded_reason():
    """Lag termination is persisted as error status with lag_exceeded reason."""
    app = FastAPI()
    app.include_router(openai_realtime_router, prefix="/v1")

    api_key = _build_api_key()
    session_router = AsyncMock()
    session_router.acquire_worker.return_value = SimpleNamespace(
        session_id="sess_lag_123",
        endpoint="ws://worker.test",
        instance="worker-1",
        runtime="parakeet",
    )
    session_router.release_worker.return_value = None

    rt = SimpleNamespace(
        routing_model="auto",
        model_runtime="parakeet",
        valid_runtimes=["parakeet"],
        effective_model="gpt-4o-transcribe",
    )

    async def _auth_db_gen():
        if False:
            yield None

    async def _db_gen():
        yield AsyncMock()

    async def _fake_get_auth_service():
        return MagicMock(), _auth_db_gen()

    async def _fake_authenticate_websocket(*args, **kwargs):
        return api_key

    async def _fake_rate_limits(*args, **kwargs):
        return True

    async def _fake_resolve_rt_routing(model):
        assert model is None
        return rt

    async def _fake_proxy_to_worker_openai(*args, **kwargs):
        raise RealtimeLagExceededError("lag_exceeded")

    async def _fake_keepalive(*args, **kwargs):
        await asyncio.sleep(3600)

    async def _fake_decrement_session_count(*args, **kwargs):
        return None

    session_service = MagicMock()
    session_service.create_session = AsyncMock()
    session_service.update_stats = AsyncMock()
    session_service.finalize_session = AsyncMock()

    with (
        patch(
            "dalston.gateway.api.v1.openai_realtime.get_session_router",
            return_value=session_router,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._get_auth_service",
            side_effect=_fake_get_auth_service,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime.authenticate_websocket",
            side_effect=_fake_authenticate_websocket,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._check_realtime_rate_limits",
            side_effect=_fake_rate_limits,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._resolve_rt_routing",
            side_effect=_fake_resolve_rt_routing,
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._proxy_to_worker_openai",
            side_effect=_fake_proxy_to_worker_openai,
        ),
        patch(
            "dalston.gateway.services.realtime_proxy.keep_session_alive",
            side_effect=_fake_keepalive,
        ),
        patch(
            "dalston.gateway.services.realtime_proxy._get_db",
            side_effect=lambda: _db_gen(),
        ),
        patch(
            "dalston.gateway.api.v1.openai_realtime._decrement_session_count",
            side_effect=_fake_decrement_session_count,
        ),
        patch(
            "dalston.gateway.services.realtime_proxy.RealtimeSessionService",
            return_value=session_service,
        ),
    ):
        client = TestClient(app)
        with client.websocket_connect(
            "/v1/realtime?intent=transcription&model=gpt-4o-transcribe",
            headers={"Authorization": "Bearer dk_test"},
        ) as websocket:
            created = websocket.receive_json()
            assert created["type"] == "transcription_session.created"

    session_service.finalize_session.assert_awaited_once()
    finalize_kwargs = session_service.finalize_session.await_args.kwargs
    assert finalize_kwargs["status"] == "error"
    assert finalize_kwargs["error"] == "lag_exceeded"
