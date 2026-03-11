"""OpenAI-compatible Real-time Transcription WebSocket endpoint.

WS /v1/realtime?intent=transcription - OpenAI Realtime API compatible endpoint

Implements the OpenAI Realtime API protocol for transcription, translating between
OpenAI's event format and Dalston's native real-time protocol.

Reference: https://platform.openai.com/docs/api-reference/realtime
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, ClassVar

import structlog
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.audio_defaults import DEFAULT_SAMPLE_RATE
from dalston.common.timeouts import (
    WS_CLOSE_TIMEOUT,
    WS_OPEN_TIMEOUT,
    WS_PING_INTERVAL,
    WS_PING_TIMEOUT,
)
from dalston.common.ws_close_codes import (
    WS_CLOSE_INVALID_REQUEST,
    WS_CLOSE_LAG_EXCEEDED,
    WS_CLOSE_RATE_LIMITED,
    WS_CLOSE_SERVICE_UNAVAILABLE,
)
from dalston.config import get_settings
from dalston.db.session import get_db as _get_db
from dalston.gateway.api.v1._realtime_common import (
    RealtimeLagExceededError,
)
from dalston.gateway.api.v1._realtime_common import (
    decrement_realtime_session_count as _decrement_session_count,
)
from dalston.gateway.api.v1._realtime_common import (
    get_realtime_auth_service as _get_auth_service,
)
from dalston.gateway.api.v1._realtime_common import (
    get_worker_close_code as _get_worker_close_code,
)
from dalston.gateway.api.v1._realtime_common import (
    resolve_rt_routing as _resolve_rt_routing,
)
from dalston.gateway.api.v1.openai_audio import (
    OpenAIEndpoint,
    build_openai_rate_limit_headers,
    is_openai_model_supported_for_endpoint,
    list_openai_models,
    raise_openai_error,
    validate_openai_request,
)
from dalston.gateway.dependencies import (
    get_db,
    get_rate_limiter,
    get_redis,
    get_security_manager,
    get_session_router,
    require_auth,
)
from dalston.gateway.middleware.auth import authenticate_websocket
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import APIKey, AuthService, Scope, SessionToken
from dalston.gateway.services.rate_limiter import RedisRateLimiter
from dalston.gateway.services.realtime_proxy import (
    ProxySessionParams,
    get_realtime_proxy,
)

logger = structlog.get_logger()


# Router for OpenAI-compatible realtime endpoint
openai_realtime_router = APIRouter(tags=["realtime", "openai"])


# =============================================================================
# OpenAI Audio Format Mapping
# =============================================================================

OPENAI_AUDIO_FORMAT_MAP = {
    "pcm16": ("pcm_s16le", 24000, DEFAULT_SAMPLE_RATE),
    "g711_ulaw": ("mulaw", 8000, 8000),
    "g711_alaw": ("alaw", 8000, 8000),
}


def map_openai_audio_format(audio_format: str) -> tuple[str, int, int]:
    """Map OpenAI format to (encoding, client_sample_rate, worker_sample_rate)."""
    return OPENAI_AUDIO_FORMAT_MAP.get(
        audio_format,
        ("pcm_s16le", 24000, DEFAULT_SAMPLE_RATE),
    )


# =============================================================================
# Event ID Generation
# =============================================================================


def generate_event_id() -> str:
    """Generate OpenAI-style event ID."""
    return f"evt_{uuid.uuid4().hex[:12]}"


def generate_item_id() -> str:
    """Generate OpenAI-style item ID."""
    return f"item_{uuid.uuid4().hex[:12]}"


def generate_session_id() -> str:
    """Generate OpenAI-style session ID."""
    return f"sess_{uuid.uuid4().hex[:12]}"


# =============================================================================
# Shared Session State
# =============================================================================


@dataclass
class OpenAISessionState:
    """Shared state between client-to-worker and worker-to-client tasks.

    Used to correlate item_ids between committed events and transcript events.
    """

    MAX_COMMITTED_QUEUE_DEPTH: ClassVar[int] = int(
        os.environ.get("DALSTON_OPENAI_RT_MAX_COMMITTED_ITEMS", "64")
    )

    pending_item_id: str = field(default_factory=generate_item_id)
    active_transcript_item_id: str | None = None
    last_committed_item_id: str | None = None
    committed_item_queue: list[str] = field(default_factory=list)

    @property
    def current_item_id(self) -> str:
        """Backwards-compatible alias for tests and existing callers."""
        if self.active_transcript_item_id is not None:
            return self.active_transcript_item_id
        return self.pending_item_id

    @current_item_id.setter
    def current_item_id(self, value: str) -> None:
        self.pending_item_id = value
        self.active_transcript_item_id = value

    @property
    def previous_item_id(self) -> str | None:
        """Backwards-compatible alias for last committed item id."""
        return self.last_committed_item_id

    @previous_item_id.setter
    def previous_item_id(self, value: str | None) -> None:
        self.last_committed_item_id = value

    def ensure_active_item(self) -> str:
        """Return active item id, initializing from pending when needed."""
        if self.active_transcript_item_id is not None:
            return self.active_transcript_item_id
        if self.committed_item_queue:
            self.active_transcript_item_id = self.committed_item_queue[0]
            return self.active_transcript_item_id
        self.active_transcript_item_id = self.pending_item_id
        return self.active_transcript_item_id

    def commit_active_item(self) -> tuple[str, str | None]:
        """Commit current active item and rotate pending item."""
        item_id = self.pending_item_id
        previous_item_id = self.last_committed_item_id
        self.last_committed_item_id = item_id
        self.committed_item_queue.append(item_id)
        if len(self.committed_item_queue) > self.MAX_COMMITTED_QUEUE_DEPTH:
            dropped_item_id = self.committed_item_queue.pop(0)
            logger.warning(
                "openai_realtime_item_queue_overflow",
                max_depth=self.MAX_COMMITTED_QUEUE_DEPTH,
                dropped_item_id=dropped_item_id,
                queue_depth=len(self.committed_item_queue),
            )
            if self.active_transcript_item_id == dropped_item_id:
                self.active_transcript_item_id = (
                    self.committed_item_queue[0] if self.committed_item_queue else None
                )
        if self.active_transcript_item_id is None:
            self.active_transcript_item_id = item_id
        self.pending_item_id = generate_item_id()
        return item_id, previous_item_id

    def finalize_active_item(self) -> None:
        """Clear active item after final transcript emission."""
        if self.active_transcript_item_id is None:
            return
        active_item_id = self.active_transcript_item_id
        if self.committed_item_queue and self.committed_item_queue[0] == active_item_id:
            self.committed_item_queue.pop(0)
        elif active_item_id in self.committed_item_queue:
            self.committed_item_queue.remove(active_item_id)
        self.active_transcript_item_id = (
            self.committed_item_queue[0] if self.committed_item_queue else None
        )

    def discard_pending_item(self) -> None:
        """Discard current active/pending item (input_audio_buffer.clear)."""
        # Keep committed in-flight item stable if a final transcript is pending.
        if (
            self.active_transcript_item_id is not None
            and self.active_transcript_item_id in self.committed_item_queue
        ):
            self.pending_item_id = generate_item_id()
            return
        self.active_transcript_item_id = None
        self.pending_item_id = generate_item_id()


# =============================================================================
# Helper Functions
# =============================================================================


async def _check_realtime_rate_limits(
    websocket: WebSocket,
    tenant_id,
) -> bool:
    """Check rate limits for realtime WebSocket connections."""
    settings = get_settings()
    db_gen = _get_db()
    try:
        db = await db_gen.__anext__()
        rate_limiter = await get_rate_limiter(settings=settings, db=db)
    finally:
        await db_gen.aclose()

    sessions_result = await rate_limiter.check_concurrent_sessions(tenant_id)
    if not sessions_result.allowed:
        await websocket.send_json(
            {
                "type": "error",
                "event_id": generate_event_id(),
                "error": {
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                    "message": f"Concurrent session limit exceeded ({sessions_result.limit} max)",
                },
            }
        )
        await websocket.close(code=WS_CLOSE_RATE_LIMITED, reason="Rate limit exceeded")
        return False

    await rate_limiter.increment_concurrent_sessions(tenant_id)
    return True


def _parse_client_secret_ttl(client_secret: object) -> int:
    """Parse optional client-secret expiration config from REST create payload."""
    default_ttl = 600
    if client_secret is None:
        return default_ttl
    if not isinstance(client_secret, dict):
        raise_openai_error(
            400,
            "client_secret must be an object",
            param="client_secret",
            code="invalid_request",
        )

    expires_cfg = client_secret.get("expires_at") or client_secret.get("expires_after")
    if expires_cfg is None:
        return default_ttl
    if not isinstance(expires_cfg, dict):
        raise_openai_error(
            400,
            "client_secret.expires_at must be an object",
            param="client_secret",
            code="invalid_request",
        )

    seconds = expires_cfg.get("seconds")
    if seconds is None:
        return default_ttl
    if not isinstance(seconds, int):
        raise_openai_error(
            400,
            "client_secret.expires_at.seconds must be an integer",
            param="client_secret",
            code="invalid_request",
        )
    if seconds < 10 or seconds > 7200:
        raise_openai_error(
            400,
            "client_secret.expires_at.seconds must be between 10 and 7200",
            param="client_secret",
            code="invalid_request",
        )
    return seconds


def _normalize_turn_detection(turn_detection: object) -> dict | None:
    """Validate and normalize turn detection config for REST create responses."""
    if turn_detection is None:
        return None
    if not isinstance(turn_detection, dict):
        raise_openai_error(
            400,
            "turn_detection must be an object or null",
            param="turn_detection",
            code="invalid_request",
        )

    turn_type = turn_detection.get("type", "server_vad")
    if turn_type != "server_vad":
        raise_openai_error(
            400,
            "Only turn_detection.type='server_vad' is currently supported",
            param="turn_detection",
            code="invalid_request",
        )

    threshold = turn_detection.get("threshold", 0.5)
    silence_duration_ms = turn_detection.get("silence_duration_ms", 500)
    prefix_padding_ms = turn_detection.get("prefix_padding_ms", 300)

    if not isinstance(threshold, float | int) or not 0.0 <= float(threshold) <= 1.0:
        raise_openai_error(
            400,
            "turn_detection.threshold must be between 0.0 and 1.0",
            param="turn_detection",
            code="invalid_request",
        )
    if not isinstance(silence_duration_ms, int) or silence_duration_ms < 0:
        raise_openai_error(
            400,
            "turn_detection.silence_duration_ms must be a non-negative integer",
            param="turn_detection",
            code="invalid_request",
        )
    if not isinstance(prefix_padding_ms, int) or prefix_padding_ms < 0:
        raise_openai_error(
            400,
            "turn_detection.prefix_padding_ms must be a non-negative integer",
            param="turn_detection",
            code="invalid_request",
        )

    return {
        "type": "server_vad",
        "threshold": float(threshold),
        "silence_duration_ms": silence_duration_ms,
        "prefix_padding_ms": prefix_padding_ms,
    }


def _normalize_noise_reduction(noise_reduction: object) -> dict | None:
    """Validate and normalize noise reduction config."""
    if noise_reduction is None:
        return None
    if not isinstance(noise_reduction, dict):
        raise_openai_error(
            400,
            "input_audio_noise_reduction must be an object or null",
            param="input_audio_noise_reduction",
            code="invalid_request",
        )
    noise_type = noise_reduction.get("type")
    if noise_type not in {"near_field", "far_field"}:
        raise_openai_error(
            400,
            "input_audio_noise_reduction.type must be near_field or far_field",
            param="input_audio_noise_reduction",
            code="invalid_request",
        )
    return {"type": noise_type}


# =============================================================================
# REST + WebSocket Endpoints
# =============================================================================


@openai_realtime_router.post(
    "/realtime/transcription_sessions",
    summary="Create OpenAI-compatible realtime transcription session",
    description=(
        "Create an ephemeral realtime client_secret for OpenAI-compatible "
        "realtime transcription clients."
    ),
)
async def create_openai_realtime_transcription_session(
    request: Request,
    response: Response,
    api_identity: Annotated[APIKey | SessionToken, Depends(require_auth)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    rate_limiter: Annotated[RedisRateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict:
    """OpenAI-compatible REST setup endpoint for realtime transcription sessions."""
    principal = (
        Principal.from_session_token(api_identity)
        if isinstance(api_identity, SessionToken)
        else Principal.from_api_key(api_identity)
    )
    security_manager.require_permission(principal, Permission.SESSION_CREATE)

    rate_result = await rate_limiter.check_request_rate(principal.tenant_id)
    headers = build_openai_rate_limit_headers(
        limit=rate_result.limit,
        remaining=rate_result.remaining,
        reset_seconds=rate_result.reset_seconds,
    )
    if not rate_result.allowed:
        headers["Retry-After"] = str(rate_result.reset_seconds)
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "message": "Rate limit exceeded",
                    "type": "rate_limit_error",
                    "param": None,
                    "code": "rate_limit_exceeded",
                }
            },
            headers=headers,
        )
    response.headers.update(headers)

    # Session tokens are already ephemeral and cannot mint further child tokens.
    if isinstance(api_identity, SessionToken):
        raise_openai_error(
            403,
            "Session token cannot create another client_secret",
            param="Authorization",
            code="invalid_session_token",
        )

    try:
        raw_body = await request.json()
    except json.JSONDecodeError as exc:
        raise_openai_error(
            400,
            f"Invalid JSON body: {exc}",
            param="body",
            code="invalid_json",
        )

    if raw_body is None:
        raw_body = {}
    if not isinstance(raw_body, dict):
        raise_openai_error(
            400,
            "Request body must be a JSON object",
            param="body",
            code="invalid_request",
        )

    if "session" in raw_body:
        if not isinstance(raw_body["session"], dict):
            raise_openai_error(
                400,
                "session must be a JSON object",
                param="session",
                code="invalid_request",
            )
        session_input = raw_body["session"]
    else:
        session_input = raw_body

    try:
        normalized_session = _normalize_session_update_payload(
            {"session": session_input}
        )
    except ValueError as exc:
        raise_openai_error(
            400,
            str(exc),
            param="session",
            code="invalid_request",
        )
    transcription_cfg = normalized_session.get("input_audio_transcription", {})
    if not isinstance(transcription_cfg, dict):
        raise_openai_error(
            400,
            "input_audio_transcription must be an object",
            param="input_audio_transcription",
            code="invalid_request",
        )

    model = transcription_cfg.get("model", "gpt-4o-transcribe")
    if not isinstance(model, str) or not model:
        raise_openai_error(
            400,
            "input_audio_transcription.model must be a non-empty string",
            param="input_audio_transcription.model",
            code="invalid_request",
        )

    prompt = transcription_cfg.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise_openai_error(
            400,
            "input_audio_transcription.prompt must be a string",
            param="input_audio_transcription.prompt",
            code="invalid_request",
        )

    include = raw_body.get("include")
    if include is not None:
        if not isinstance(include, list) or not all(
            isinstance(item, str) for item in include
        ):
            raise_openai_error(
                400,
                "include must be an array of strings",
                param="include",
                code="invalid_request",
            )

    validate_openai_request(
        model=model,
        response_format=None,
        timestamp_granularities=None,
        endpoint=OpenAIEndpoint.REALTIME,
        prompt=prompt,
        include=include,
    )

    input_audio_format = normalized_session.get("input_audio_format", "pcm16")
    if input_audio_format not in OPENAI_AUDIO_FORMAT_MAP:
        raise_openai_error(
            400,
            f"Invalid input_audio_format: {input_audio_format}",
            param="input_audio_format",
            code="invalid_request",
        )

    modalities = raw_body.get("modalities", ["text"])
    if not isinstance(modalities, list) or not all(
        isinstance(m, str) and m in {"text", "audio"} for m in modalities
    ):
        raise_openai_error(
            400,
            "modalities must be an array containing 'text' and/or 'audio'",
            param="modalities",
            code="invalid_request",
        )

    turn_detection = _normalize_turn_detection(
        normalized_session.get("turn_detection", None)
    )
    noise_reduction = _normalize_noise_reduction(
        normalized_session.get("input_audio_noise_reduction", None)
    )

    ttl = _parse_client_secret_ttl(raw_body.get("client_secret"))
    auth_service = AuthService(db, redis)
    raw_token, session_token = await auth_service.create_session_token(
        api_key=api_identity,
        ttl=ttl,
        scopes=[Scope.REALTIME],
    )

    response_payload: dict = {
        "client_secret": {
            "value": raw_token,
            "expires_at": int(session_token.expires_at.timestamp()),
        },
        "input_audio_format": input_audio_format,
        "input_audio_transcription": {
            "model": model,
        },
        "modalities": modalities,
        "turn_detection": turn_detection,
        "input_audio_noise_reduction": noise_reduction,
    }
    language = transcription_cfg.get("language")
    if isinstance(language, str) and language:
        response_payload["input_audio_transcription"]["language"] = language
    if prompt:
        response_payload["input_audio_transcription"]["prompt"] = prompt
    if include:
        response_payload["include"] = include
    response_payload["created_at"] = int(datetime.now(UTC).timestamp())

    return response_payload


@openai_realtime_router.websocket("/realtime")
async def openai_realtime_transcription(
    websocket: WebSocket,
    intent: Annotated[
        str, Query(description="Intent type (must be 'transcription')")
    ] = "transcription",
    model: Annotated[str, Query(description="Model ID")] = "gpt-4o-transcribe",
    # OpenAI-Beta header (optional, for compatibility)
    openai_beta: Annotated[str | None, Header(alias="OpenAI-Beta")] = None,
):
    """OpenAI-compatible WebSocket endpoint for real-time transcription.

    Implements the OpenAI Realtime API protocol for transcription.

    Connection URL:
        WS /v1/realtime?intent=transcription&model=gpt-4o-transcribe

    Headers:
        Authorization: Bearer <api_key>
        OpenAI-Beta: realtime=v1 (optional)

    Protocol:
        Client sends:
            - transcription_session.update: Configure session
            - input_audio_buffer.append: Send audio data
            - input_audio_buffer.commit: Force processing
            - input_audio_buffer.clear: Clear buffer

        Server sends:
            - transcription_session.created: Session started
            - transcription_session.updated: Config acknowledged
            - input_audio_buffer.speech_started: VAD detected speech
            - input_audio_buffer.speech_stopped: VAD detected silence
            - input_audio_buffer.committed: Buffer committed
            - conversation.item.input_audio_transcription.delta: Partial transcript
            - conversation.item.input_audio_transcription.completed: Final transcript
            - error: Error occurred
    """
    # Validate intent
    if intent != "transcription":
        await websocket.close(
            code=WS_CLOSE_INVALID_REQUEST, reason=f"Unsupported intent: {intent}"
        )
        return

    # Validate model
    if not is_openai_model_supported_for_endpoint(model, OpenAIEndpoint.REALTIME):
        supported = ", ".join(list_openai_models(OpenAIEndpoint.REALTIME))
        await websocket.close(
            code=WS_CLOSE_INVALID_REQUEST,
            reason=f"Invalid model: {model}. Supported: {supported}",
        )
        return

    # Get session router
    try:
        session_router = get_session_router()
    except Exception:
        await websocket.close(
            code=WS_CLOSE_SERVICE_UNAVAILABLE, reason="Service unavailable"
        )
        return

    # Authenticate BEFORE accepting the connection
    auth_service, db_gen = await _get_auth_service()
    try:
        api_key = await authenticate_websocket(
            websocket, auth_service, required_scope=Scope.REALTIME
        )
        if api_key is None:
            return
    finally:
        await db_gen.aclose()

    # Accept connection
    await websocket.accept()

    # Check rate limits – increments the counter
    if not await _check_realtime_rate_limits(websocket, api_key.tenant_id):
        return

    # Everything from here must decrement the session counter on exit.
    try:
        # OpenAI model IDs (gpt-4o-transcribe, etc.) are treated as "auto" — resolve
        # via registry to pick the largest ready model and filter valid engine_ids (M48)
        rt = await _resolve_rt_routing(None)

        # The connect callback sends transcription_session.created then proxies.
        async def _openai_connect(ws, alloc):
            openai_session_id = generate_session_id()
            await ws.send_json(
                {
                    "type": "transcription_session.created",
                    "event_id": generate_event_id(),
                    "session": {
                        "id": openai_session_id,
                        "model": model,
                        "input_audio_format": "pcm16",
                        "input_audio_transcription": {"model": model},
                        "turn_detection": None,
                        "input_audio_noise_reduction": None,
                    },
                }
            )
            return await _proxy_to_worker_openai(
                client_ws=ws,
                worker_endpoint=alloc.endpoint,
                session_id=alloc.session_id,
                openai_session_id=openai_session_id,
                model=rt.effective_model,
            )

        await get_realtime_proxy().run(
            websocket=websocket,
            session_router=session_router,
            routing_params=rt,
            language="auto",  # Updated inside the session via transcription_session.update
            connect=_openai_connect,
            session_params=ProxySessionParams(
                tenant_id=api_key.tenant_id,
                client_ip=websocket.client.host if websocket.client else "unknown",
                language="auto",
                model=model,
                created_by_key_id=api_key.id,
            ),
            on_no_capacity=lambda: websocket.send_json(
                {
                    "type": "error",
                    "event_id": generate_event_id(),
                    "error": {
                        "type": "server_error",
                        "code": "no_capacity",
                        "message": "No realtime workers available. Try again later.",
                    },
                }
            ),
        )
    finally:
        await _decrement_session_count(api_key.tenant_id)


# =============================================================================
# OpenAI Protocol Translation - Proxy to Worker
# =============================================================================


async def _proxy_to_worker_openai(
    client_ws: WebSocket,
    worker_endpoint: str,
    session_id: str,
    openai_session_id: str,
    model: str,
) -> dict | None:
    """Proxy with OpenAI Realtime API protocol translation.

    Returns:
        Session end data if received from worker, None otherwise
    """
    from urllib.parse import urlencode

    import websockets

    # Session configuration state (updated via transcription/session.update)
    session_config = {
        "language": "auto",
        "encoding": "pcm_s16le",
        "client_sample_rate": 24000,
        "sample_rate": DEFAULT_SAMPLE_RATE,
        "enable_vad": True,
        "interim_results": True,
        "word_timestamps": False,
        "prompt": None,
        "vocabulary": None,
        "vad_threshold": 0.5,
        "min_silence_duration_ms": 500,
        "prefix_padding_ms": 300,
        "noise_reduction": None,
    }

    # Shared state for item_id correlation between tasks
    session_state = OpenAISessionState()

    # Build initial worker URL
    params = _build_worker_params(session_id, session_config)
    worker_url = f"{worker_endpoint}/session?{urlencode(params)}"

    async with websockets.connect(
        worker_url,
        open_timeout=WS_OPEN_TIMEOUT,
        close_timeout=WS_CLOSE_TIMEOUT,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
    ) as worker_ws:
        # Create translation tasks
        client_to_worker = asyncio.create_task(
            _openai_client_to_worker(
                client_ws, worker_ws, session_id, session_config, session_state
            )
        )
        worker_to_client = asyncio.create_task(
            _openai_worker_to_client(
                worker_ws, client_ws, session_id, openai_session_id, session_state
            )
        )

        done, pending = await asyncio.wait(
            [client_to_worker, worker_to_client],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If client finished first, wait for worker to send session.end
        session_end_data = None
        if client_to_worker in done and worker_to_client in pending:
            logger.debug(
                "waiting_for_session_end",
                session_id=session_id,
                msg="Client finished, waiting for worker session.end",
            )
            try:
                await asyncio.wait_for(worker_to_client, timeout=10.0)
                session_end_data = worker_to_client.result()
            except TimeoutError:
                logger.warning(
                    "session_end_timeout",
                    session_id=session_id,
                    msg="Worker didn't send session.end in 10s",
                )
                worker_to_client.cancel()
                try:
                    await worker_to_client
                except asyncio.CancelledError:
                    pass
            except Exception as e:
                logger.warning("session_end_error", session_id=session_id, error=str(e))
        else:
            # Worker finished first - get result if available
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if worker_to_client in done:
                try:
                    session_end_data = worker_to_client.result()
                except Exception as e:
                    logger.warning(
                        "session_end_result_error", session_id=session_id, error=str(e)
                    )

        # Check for exceptions in completed tasks
        for task in done:
            if task is not worker_to_client:
                exc = task.exception()
                if exc is not None and session_end_data is None:
                    raise exc

        if (
            session_end_data is None
            and _get_worker_close_code(worker_ws) == WS_CLOSE_LAG_EXCEEDED
        ):
            raise RealtimeLagExceededError("lag_exceeded")

        return session_end_data


def _build_worker_params(session_id: str, config: dict) -> dict[str, str]:
    """Build worker URL parameters from session config."""
    params = {
        "session_id": session_id,
        "language": config["language"],
        "model": config.get("model", ""),
        "encoding": config["encoding"],
        "sample_rate": str(config["sample_rate"]),
        "client_sample_rate": str(config["client_sample_rate"]),
        "enable_vad": str(config["enable_vad"]).lower(),
        "interim_results": str(config["interim_results"]).lower(),
        "word_timestamps": str(config["word_timestamps"]).lower(),
        "vad_threshold": str(config["vad_threshold"]),
        "min_silence_duration_ms": str(config["min_silence_duration_ms"]),
        "prefix_padding_ms": str(config["prefix_padding_ms"]),
    }
    if config.get("vocabulary"):
        params["vocabulary"] = json.dumps(config["vocabulary"])
    return params


def _normalize_session_update_payload(data: dict) -> dict:
    """Accept both legacy and nested OpenAI session update payload variants."""
    raw_session = data.get("session")
    if raw_session is None:
        # Accept flat payload shape:
        # {"type":"transcription_session.update", "input_audio_format":"pcm16", ...}
        session: dict = {k: v for k, v in data.items() if k not in {"type", "event_id"}}
    elif isinstance(raw_session, dict):
        session = dict(raw_session)
        if isinstance(session.get("session"), dict):
            raise ValueError("Nested payload {'session': {'session': ...}} is invalid")
    else:
        return {}

    normalized = dict(session)
    audio = session.get("audio")
    if isinstance(audio, dict):
        audio_input = audio.get("input")
        if isinstance(audio_input, dict):
            if "format" in audio_input:
                normalized["input_audio_format"] = audio_input["format"]
            if isinstance(audio_input.get("transcription"), dict):
                normalized["input_audio_transcription"] = audio_input["transcription"]
            if "turn_detection" in audio_input:
                normalized["turn_detection"] = audio_input["turn_detection"]
            if "noise_reduction" in audio_input:
                normalized["input_audio_noise_reduction"] = audio_input[
                    "noise_reduction"
                ]

    return normalized


# =============================================================================
# OpenAI Client → Worker Translation
# =============================================================================


async def _openai_client_to_worker(
    client_ws: WebSocket,
    worker_ws,
    session_id: str,
    session_config: dict,
    session_state: OpenAISessionState,
) -> None:
    """Translate OpenAI client messages to Dalston worker format.

    OpenAI sends:
        - transcription_session.update: Session configuration
        - input_audio_buffer.append: {"audio": "<base64>"}
        - input_audio_buffer.commit: Force processing
        - input_audio_buffer.clear: Clear buffer

    Dalston expects:
        - Binary audio frames
        - {"type": "commit"} / {"type": "end"}
    """
    end_sent = False
    try:
        while True:
            message = await client_ws.receive()

            if message["type"] == "websocket.disconnect":
                await worker_ws.send(json.dumps({"type": "end"}))
                end_sent = True
                break

            elif message["type"] == "websocket.receive":
                if "text" in message:
                    try:
                        data = json.loads(message["text"])
                        msg_type = data.get("type")

                        if msg_type in {
                            "transcription_session.update",
                            "session.update",
                        }:
                            # Update session config and forward to worker
                            await _handle_session_update(
                                client_ws, worker_ws, data, session_config
                            )

                        elif msg_type == "input_audio_buffer.append":
                            # Decode base64 audio and send as binary
                            audio_b64 = data.get("audio", "")
                            if audio_b64:
                                audio_bytes = base64.b64decode(audio_b64)
                                await worker_ws.send(audio_bytes)

                        elif msg_type == "input_audio_buffer.commit":
                            # Force processing of buffered audio
                            await worker_ws.send(json.dumps({"type": "flush"}))
                            current_item_id, previous_item_id = (
                                session_state.commit_active_item()
                            )
                            await client_ws.send_json(
                                {
                                    "type": "conversation.item.created",
                                    "event_id": generate_event_id(),
                                    "previous_item_id": previous_item_id,
                                    "item": {
                                        "id": current_item_id,
                                        "type": "message",
                                        "role": "user",
                                        "content": [{"type": "input_audio"}],
                                    },
                                }
                            )
                            await client_ws.send_json(
                                {
                                    "type": "input_audio_buffer.committed",
                                    "event_id": generate_event_id(),
                                    "item_id": current_item_id,
                                    "previous_item_id": previous_item_id,
                                }
                            )

                        elif msg_type == "input_audio_buffer.clear":
                            # Clear buffer - discard without transcription
                            await worker_ws.send(json.dumps({"type": "clear"}))
                            session_state.discard_pending_item()
                            await client_ws.send_json(
                                {
                                    "type": "input_audio_buffer.cleared",
                                    "event_id": generate_event_id(),
                                }
                            )

                    except json.JSONDecodeError as e:
                        logger.debug(
                            "openai_json_parse_error",
                            session_id=session_id,
                            error=str(e),
                        )
                        await client_ws.send_json(
                            {
                                "type": "error",
                                "event_id": generate_event_id(),
                                "error": {
                                    "type": "invalid_request_error",
                                    "code": "invalid_json",
                                    "message": f"Invalid JSON: {e}",
                                },
                            }
                        )
                    except binascii.Error as e:
                        logger.debug(
                            "openai_base64_decode_error",
                            session_id=session_id,
                            error=str(e),
                        )
                        await client_ws.send_json(
                            {
                                "type": "error",
                                "event_id": generate_event_id(),
                                "error": {
                                    "type": "invalid_request_error",
                                    "code": "invalid_audio",
                                    "message": "Invalid base64 audio data",
                                },
                            }
                        )

                elif "bytes" in message:
                    # Raw binary audio - pass through directly
                    await worker_ws.send(message["bytes"])

    except Exception as e:
        logger.exception(
            "openai_client_to_worker_ended",
            session_id=session_id,
            error=str(e),
        )
        if not end_sent:
            try:
                await worker_ws.send(json.dumps({"type": "end"}))
            except Exception:
                pass
        raise


async def _handle_session_update(
    client_ws: WebSocket,
    worker_ws,
    data: dict,
    session_config: dict,
) -> None:
    """Handle transcription_session.update event.

    Updates session configuration, forwards to worker, and sends acknowledgment.
    """
    try:
        session = _normalize_session_update_payload(data)
    except ValueError as exc:
        await client_ws.send_json(
            {
                "type": "error",
                "event_id": generate_event_id(),
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_request",
                    "message": str(exc),
                },
            }
        )
        return

    # Update audio format
    audio_format = session.get("input_audio_format", "pcm16")
    encoding, client_sample_rate, sample_rate = map_openai_audio_format(audio_format)
    session_config["encoding"] = encoding
    session_config["client_sample_rate"] = client_sample_rate
    session_config["sample_rate"] = sample_rate
    session_config.setdefault("vad_threshold", 0.5)
    session_config.setdefault("min_silence_duration_ms", 500)
    session_config.setdefault("prefix_padding_ms", 300)
    session_config.setdefault("noise_reduction", None)

    # Update transcription settings
    transcription = session.get("input_audio_transcription")
    if not isinstance(transcription, dict):
        transcription = {}
    if "language" in transcription:
        session_config["language"] = transcription["language"]
    if "prompt" in transcription:
        # Preserve prompt as free text in session state (not hotword splitting).
        prompt = transcription["prompt"]
        session_config["prompt"] = (
            prompt if isinstance(prompt, str) and prompt else None
        )

    # Update turn detection (VAD)
    turn_detection = session.get("turn_detection")
    if "turn_detection" in session:
        if turn_detection is None:
            session_config["enable_vad"] = False
        elif isinstance(turn_detection, dict):
            session_config["enable_vad"] = True
            if "threshold" in turn_detection:
                session_config["vad_threshold"] = turn_detection["threshold"]
            if "silence_duration_ms" in turn_detection:
                session_config["min_silence_duration_ms"] = turn_detection[
                    "silence_duration_ms"
                ]
            if "prefix_padding_ms" in turn_detection:
                session_config["prefix_padding_ms"] = turn_detection[
                    "prefix_padding_ms"
                ]

    noise_reduction = session.get("input_audio_noise_reduction")
    if noise_reduction is not None:
        session_config["noise_reduction"] = noise_reduction

    # Forward config update to worker
    await worker_ws.send(
        json.dumps(
            {
                "type": "config",
                "language": session_config["language"],
                "vad_threshold": session_config["vad_threshold"],
                "min_silence_duration_ms": session_config["min_silence_duration_ms"],
                "prefix_padding_ms": session_config["prefix_padding_ms"],
            }
        )
    )

    # Send session updated acknowledgment
    await client_ws.send_json(
        {
            "type": "transcription_session.updated",
            "event_id": generate_event_id(),
            "session": {
                "input_audio_format": audio_format,
                "input_audio_transcription": {
                    "model": transcription.get("model", "gpt-4o-transcribe"),
                    "language": session_config["language"],
                    "prompt": session_config.get("prompt"),
                },
                "turn_detection": turn_detection,
                "input_audio_noise_reduction": session_config["noise_reduction"],
            },
        }
    )


# =============================================================================
# Worker → OpenAI Client Translation
# =============================================================================


async def _openai_worker_to_client(
    worker_ws,
    client_ws: WebSocket,
    session_id: str,
    openai_session_id: str,
    session_state: OpenAISessionState,
) -> dict | None:
    """Translate Dalston worker messages to OpenAI client format.

    Dalston sends:
        - session.begin: Session started
        - transcript.partial: Incremental transcript
        - transcript.final: Final transcript
        - vad.speech_start: Speech detected
        - vad.speech_end: Silence detected
        - session.end: Session ended
        - error: Error occurred

    OpenAI expects:
        - input_audio_buffer.speech_started
        - input_audio_buffer.speech_stopped
        - conversation.item.input_audio_transcription.delta
        - conversation.item.input_audio_transcription.completed
        - error

    Returns:
        Session end data if received, None otherwise
    """
    # Use shared state for item_id correlation with committed events
    session_end_data = None
    client_closed = False

    try:
        async for message in worker_ws:
            if isinstance(message, bytes):
                continue  # Skip binary messages

            try:
                data = json.loads(message)
                msg_type = data.get("type")
                translated = None

                if msg_type == "session.begin":
                    # Already sent transcription_session.created, skip
                    pass

                elif msg_type == "transcript.partial":
                    item_id = session_state.ensure_active_item()
                    translated = {
                        "type": "conversation.item.input_audio_transcription.delta",
                        "event_id": generate_event_id(),
                        "item_id": item_id,
                        "content_index": 0,
                        "delta": data.get("text", ""),
                    }

                elif msg_type == "transcript.final":
                    item_id = session_state.ensure_active_item()
                    translated = {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "event_id": generate_event_id(),
                        "item_id": item_id,
                        "content_index": 0,
                        "transcript": data.get("text", ""),
                    }
                    session_state.finalize_active_item()

                elif msg_type == "vad.speech_start":
                    item_id = session_state.ensure_active_item()
                    translated = {
                        "type": "input_audio_buffer.speech_started",
                        "event_id": generate_event_id(),
                        "item_id": item_id,
                        "audio_start_ms": int(data.get("timestamp", 0) * 1000),
                    }

                elif msg_type == "vad.speech_end":
                    item_id = session_state.ensure_active_item()
                    translated = {
                        "type": "input_audio_buffer.speech_stopped",
                        "event_id": generate_event_id(),
                        "item_id": item_id,
                        "audio_end_ms": int(data.get("timestamp", 0) * 1000),
                    }

                elif msg_type == "session.end":
                    # Capture session end data for stats
                    session_end_data = data
                    break

                elif msg_type == "error":
                    translated = {
                        "type": "error",
                        "event_id": generate_event_id(),
                        "error": {
                            "type": "server_error",
                            "code": data.get("code", "processing_failed"),
                            "message": data.get("message", "Unknown error"),
                        },
                    }

                elif msg_type == "warning":
                    translated = {
                        "type": "warning",
                        "event_id": generate_event_id(),
                        "warning": {
                            "code": data.get("code", "warning"),
                            "message": data.get("message", ""),
                        },
                    }
                    if "lag_seconds" in data:
                        translated["warning"]["lag_seconds"] = data.get(
                            "lag_seconds", 0
                        )
                    if "warning_threshold_seconds" in data:
                        translated["warning"]["warning_threshold_seconds"] = data.get(
                            "warning_threshold_seconds", 0
                        )
                    if "hard_threshold_seconds" in data:
                        translated["warning"]["hard_threshold_seconds"] = data.get(
                            "hard_threshold_seconds", 0
                        )

                elif msg_type == "session.terminated":
                    translated = {
                        "type": "error",
                        "event_id": generate_event_id(),
                        "error": {
                            "type": "server_error",
                            "code": data.get("reason", "session_terminated"),
                            "message": "Realtime session terminated",
                        },
                    }

                if translated and not client_closed:
                    try:
                        await client_ws.send_json(translated)
                    except Exception:
                        client_closed = True

            except json.JSONDecodeError:
                pass

    except Exception as e:
        logger.exception(
            "openai_worker_to_client_ended",
            session_id=session_id,
            error=str(e),
        )
        if session_end_data is None:
            raise

    return session_end_data
