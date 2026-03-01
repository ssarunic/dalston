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
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect

from dalston.common.redis import get_redis as _get_redis
from dalston.config import get_settings
from dalston.db.session import get_db as _get_db
from dalston.gateway.dependencies import get_session_router
from dalston.gateway.middleware.auth import authenticate_websocket
from dalston.gateway.services.auth import AuthService, Scope
from dalston.gateway.services.rate_limiter import RedisRateLimiter
from dalston.gateway.services.realtime_sessions import RealtimeSessionService

logger = structlog.get_logger()

# Router for OpenAI-compatible realtime endpoint
openai_realtime_router = APIRouter(tags=["realtime", "openai"])


# =============================================================================
# OpenAI Model Mapping for Real-time
# =============================================================================

OPENAI_REALTIME_MODEL_MAP = {
    "gpt-4o-transcribe": None,  # None = auto-select best available
    "gpt-4o-mini-transcribe": None,  # None = auto-select
    "whisper-1": None,  # None = auto-select
}


def is_openai_realtime_model(model: str) -> bool:
    """Check if model is a valid OpenAI real-time model."""
    return model in OPENAI_REALTIME_MODEL_MAP


def map_openai_realtime_model(model: str) -> str | None:
    """Map OpenAI model to Dalston engine ID.

    Returns None for auto-selection.
    """
    return OPENAI_REALTIME_MODEL_MAP.get(model)


# =============================================================================
# OpenAI Audio Format Mapping
# =============================================================================

OPENAI_AUDIO_FORMAT_MAP = {
    # Note: OpenAI spec uses 24kHz but our Silero VAD only supports 8kHz/16kHz
    # Default to 16kHz for compatibility with our realtime workers
    "pcm16": ("pcm_s16le", 16000),  # 16-bit PCM, 16kHz (Silero VAD compatible)
    "g711_ulaw": ("mulaw", 8000),
    "g711_alaw": ("alaw", 8000),
}


def map_openai_audio_format(audio_format: str) -> tuple[str, int]:
    """Map OpenAI audio format to Dalston encoding and sample rate."""
    return OPENAI_AUDIO_FORMAT_MAP.get(audio_format, ("pcm_s16le", 24000))


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
# Helper Functions
# =============================================================================


async def _get_auth_service() -> tuple[AuthService, Any]:
    """Get AuthService for WebSocket authentication."""
    redis = await _get_redis()
    db_gen = _get_db()
    db = await db_gen.__anext__()
    return AuthService(db, redis), db_gen


async def _check_realtime_rate_limits(
    websocket: WebSocket,
    tenant_id,
) -> bool:
    """Check rate limits for realtime WebSocket connections."""
    settings = get_settings()
    redis = await _get_redis()
    rate_limiter = RedisRateLimiter(
        redis=redis,
        requests_per_minute=settings.rate_limit_requests_per_minute,
        max_concurrent_jobs=settings.rate_limit_concurrent_jobs,
        max_concurrent_sessions=settings.rate_limit_concurrent_sessions,
    )

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
        await websocket.close(code=4429, reason="Rate limit exceeded")
        return False

    await rate_limiter.increment_concurrent_sessions(tenant_id)
    return True


async def _decrement_session_count(tenant_id) -> None:
    """Decrement concurrent session count when connection closes."""
    try:
        settings = get_settings()
        redis = await _get_redis()
        rate_limiter = RedisRateLimiter(
            redis=redis,
            requests_per_minute=settings.rate_limit_requests_per_minute,
            max_concurrent_jobs=settings.rate_limit_concurrent_jobs,
            max_concurrent_sessions=settings.rate_limit_concurrent_sessions,
        )
        await rate_limiter.decrement_concurrent_sessions(tenant_id)
    except Exception as e:
        logger.warning("failed_to_decrement_session_count", error=str(e))


async def _keep_session_alive(
    session_router,
    session_id: str,
    interval: int = 60,
) -> None:
    """Periodically extend session TTL to prevent expiration."""
    while True:
        await asyncio.sleep(interval)
        try:
            await session_router.extend_session_ttl(session_id)
            logger.debug("session_ttl_extended", session_id=session_id)
        except Exception as e:
            logger.warning(
                "session_ttl_extend_failed", session_id=session_id, error=str(e)
            )


# =============================================================================
# WebSocket Endpoint
# =============================================================================


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
        await websocket.close(code=4400, reason=f"Unsupported intent: {intent}")
        return

    # Validate model
    if not is_openai_realtime_model(model):
        await websocket.close(
            code=4400,
            reason=f"Invalid model: {model}. Supported: gpt-4o-transcribe, gpt-4o-mini-transcribe, whisper-1",
        )
        return

    # Get session router
    try:
        session_router = get_session_router()
    except Exception:
        await websocket.close(code=4503, reason="Service unavailable")
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

    # Check rate limits
    if not await _check_realtime_rate_limits(websocket, api_key.tenant_id):
        return

    session_tenant_id = api_key.tenant_id

    allocation = None
    try:
        # Map OpenAI model to Dalston engine
        routing_model = map_openai_realtime_model(model)
        client_ip = websocket.client.host if websocket.client else "unknown"

        # Acquire worker
        allocation = await session_router.acquire_worker(
            language="auto",  # Will be updated via session config
            model=routing_model,
            client_ip=client_ip,
        )

        if allocation is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "event_id": generate_event_id(),
                    "error": {
                        "type": "server_error",
                        "code": "no_capacity",
                        "message": "No realtime workers available. Try again later.",
                    },
                }
            )
            await websocket.close(code=4503, reason="No capacity")
            return

        log = logger.bind(
            session_id=allocation.session_id,
            worker_id=allocation.worker_id,
            client_ip=client_ip,
            protocol="openai",
        )
        log.info("openai_session_allocated")

        # Persist session to database for visibility in console/API
        session_service = None
        db_gen = None
        try:
            db_gen = _get_db()
            db_session = await db_gen.__anext__()
            settings = get_settings()
            session_service = RealtimeSessionService(db_session, settings)

            await session_service.create_session(
                session_id=allocation.session_id,
                tenant_id=api_key.tenant_id,
                worker_id=allocation.worker_id,
                client_ip=client_ip,
                language="auto",
                model=model,
                engine=allocation.worker_id.rsplit("-", 1)[0]
                if allocation.worker_id
                else None,
            )
            log.debug("openai_session_persisted")
        except Exception as e:
            log.warning("openai_session_persist_failed", error=str(e))
            # Continue without persistence - session still works via Redis

        keepalive_task = None
        session_status = "completed"
        try:
            # Start keepalive task
            keepalive_task = asyncio.create_task(
                _keep_session_alive(session_router, allocation.session_id)
            )

            # Generate OpenAI-style session ID for client
            openai_session_id = generate_session_id()

            # Send session created event
            await websocket.send_json(
                {
                    "type": "transcription_session.created",
                    "event_id": generate_event_id(),
                    "session": {
                        "id": openai_session_id,
                        "model": model,
                        "input_audio_format": "pcm16",
                        "input_audio_transcription": {
                            "model": model,
                        },
                    },
                }
            )

            # Connect to worker with OpenAI protocol translation
            session_end_data = await _proxy_to_worker_openai(
                client_ws=websocket,
                worker_endpoint=allocation.endpoint,
                session_id=allocation.session_id,
                openai_session_id=openai_session_id,
                model=model,
            )

        except WebSocketDisconnect:
            log.info("openai_client_disconnected")
            session_end_data = None
            session_status = "interrupted"
        except Exception as e:
            log.error("openai_session_error", error=str(e))
            session_end_data = None
            session_status = "error"
        finally:
            if keepalive_task:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

            if allocation:
                await session_router.release_worker(allocation.session_id)
                log.info("openai_session_released")

            # If we didn't get session_end_data but status is still "completed",
            # the worker connection failed silently - mark as error
            if session_end_data is None and session_status == "completed":
                session_status = "error"
                log.warning(
                    "openai_session_no_end_data", msg="Worker didn't send session.end"
                )

            # Update session stats from session.end data
            if session_service and allocation and session_end_data:
                try:
                    audio_duration = session_end_data.get("total_audio_seconds", 0)
                    segments = session_end_data.get("segments", [])
                    transcript = session_end_data.get("transcript", "")
                    word_count = len(transcript.split()) if transcript else 0
                    transcript_uri = session_end_data.get("transcript_uri")

                    log.info(
                        "openai_session_stats",
                        audio_duration=audio_duration,
                        segment_count=len(segments),
                        word_count=word_count,
                        transcript_uri=transcript_uri,
                    )

                    await session_service.update_stats(
                        session_id=allocation.session_id,
                        audio_duration_seconds=audio_duration,
                        segment_count=len(segments),
                        word_count=word_count,
                    )
                except Exception as e:
                    log.warning("openai_session_stats_failed", error=str(e))

            # Finalize session in database
            if session_service and allocation:
                try:
                    transcript_uri = (
                        session_end_data.get("transcript_uri")
                        if session_end_data
                        else None
                    )
                    await session_service.finalize_session(
                        session_id=allocation.session_id,
                        status=session_status,
                        transcript_uri=transcript_uri,
                    )
                    log.debug("openai_session_finalized", status=session_status)
                except Exception as e:
                    log.warning("openai_session_finalize_failed", error=str(e))

            # Clean up DB session
            if db_gen:
                try:
                    await db_gen.aclose()
                except Exception:
                    pass

    finally:
        await _decrement_session_count(session_tenant_id)


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

    # Session configuration state (updated via transcription_session.update)
    # Note: Default to 16kHz as Silero VAD only supports 8kHz/16kHz
    session_config = {
        "language": "auto",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "enable_vad": True,
        "interim_results": True,
        "word_timestamps": False,
        "vocabulary": None,
    }

    # Build initial worker URL
    params = _build_worker_params(session_id, session_config)
    worker_url = f"{worker_endpoint}/session?{urlencode(params)}"

    async with websockets.connect(
        worker_url,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
    ) as worker_ws:
        # Create translation tasks
        client_to_worker = asyncio.create_task(
            _openai_client_to_worker(client_ws, worker_ws, session_id, session_config)
        )
        worker_to_client = asyncio.create_task(
            _openai_worker_to_client(
                worker_ws, client_ws, session_id, openai_session_id
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

        return session_end_data


def _build_worker_params(session_id: str, config: dict) -> dict[str, str]:
    """Build worker URL parameters from session config."""
    params = {
        "session_id": session_id,
        "language": config["language"],
        "model": "auto",
        "encoding": config["encoding"],
        "sample_rate": str(config["sample_rate"]),
        "enable_vad": str(config["enable_vad"]).lower(),
        "interim_results": str(config["interim_results"]).lower(),
        "word_timestamps": str(config["word_timestamps"]).lower(),
    }
    if config.get("vocabulary"):
        params["vocabulary"] = json.dumps(config["vocabulary"])
    return params


# =============================================================================
# OpenAI Client → Worker Translation
# =============================================================================


async def _openai_client_to_worker(
    client_ws: WebSocket,
    worker_ws,
    session_id: str,
    session_config: dict,
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

                        if msg_type == "transcription_session.update":
                            # Update session config (for future connections)
                            await _handle_session_update(
                                client_ws, data, session_config
                            )

                        elif msg_type == "input_audio_buffer.append":
                            # Decode base64 audio and send as binary
                            audio_b64 = data.get("audio", "")
                            if audio_b64:
                                audio_bytes = base64.b64decode(audio_b64)
                                await worker_ws.send(audio_bytes)

                        elif msg_type == "input_audio_buffer.commit":
                            # Force processing of buffered audio
                            await worker_ws.send(json.dumps({"type": "commit"}))
                            # Send committed acknowledgment
                            await client_ws.send_json(
                                {
                                    "type": "input_audio_buffer.committed",
                                    "event_id": generate_event_id(),
                                    "item_id": generate_item_id(),
                                }
                            )

                        elif msg_type == "input_audio_buffer.clear":
                            # Clear buffer - send flush without commit
                            await worker_ws.send(json.dumps({"type": "clear"}))
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
        logger.debug(
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
    data: dict,
    session_config: dict,
) -> None:
    """Handle transcription_session.update event.

    Updates session configuration and sends acknowledgment.
    """
    session = data.get("session", {})

    # Update audio format
    audio_format = session.get("input_audio_format", "pcm16")
    encoding, sample_rate = map_openai_audio_format(audio_format)
    session_config["encoding"] = encoding
    session_config["sample_rate"] = sample_rate

    # Update transcription settings
    transcription = session.get("input_audio_transcription", {})
    if "language" in transcription:
        session_config["language"] = transcription["language"]
    if "prompt" in transcription:
        # Convert prompt to vocabulary list
        prompt = transcription["prompt"]
        if prompt:
            # Split prompt into terms for vocabulary boosting
            session_config["vocabulary"] = [
                term.strip() for term in prompt.split(",") if term.strip()
            ]

    # Update turn detection (VAD)
    turn_detection = session.get("turn_detection")
    if turn_detection is None:
        session_config["enable_vad"] = False
    elif isinstance(turn_detection, dict):
        session_config["enable_vad"] = True
        # Could extract threshold, silence_duration_ms, etc. for future use

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
                },
                "turn_detection": turn_detection,
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
    # Track current item for delta/completed events
    current_item_id = generate_item_id()
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
                    translated = {
                        "type": "conversation.item.input_audio_transcription.delta",
                        "event_id": generate_event_id(),
                        "item_id": current_item_id,
                        "content_index": 0,
                        "delta": data.get("text", ""),
                    }

                elif msg_type == "transcript.final":
                    translated = {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "event_id": generate_event_id(),
                        "item_id": current_item_id,
                        "content_index": 0,
                        "transcript": data.get("text", ""),
                    }
                    # Generate new item ID for next utterance
                    current_item_id = generate_item_id()

                elif msg_type == "vad.speech_start":
                    translated = {
                        "type": "input_audio_buffer.speech_started",
                        "event_id": generate_event_id(),
                        "audio_start_ms": int(data.get("timestamp", 0) * 1000),
                    }

                elif msg_type == "vad.speech_end":
                    translated = {
                        "type": "input_audio_buffer.speech_stopped",
                        "event_id": generate_event_id(),
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

                if translated and not client_closed:
                    try:
                        await client_ws.send_json(translated)
                    except Exception:
                        client_closed = True

            except json.JSONDecodeError:
                pass

    except Exception as e:
        logger.debug(
            "openai_worker_to_client_ended",
            session_id=session_id,
            error=str(e),
        )
        if session_end_data is None:
            raise

    return session_end_data
