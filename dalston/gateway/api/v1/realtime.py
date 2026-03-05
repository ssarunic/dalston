"""Real-time transcription WebSocket endpoints.

WS /v1/audio/transcriptions/stream - Streaming transcription (Dalston native)
WS /v1/speech-to-text/realtime - Streaming transcription (ElevenLabs compatible)
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from dalston.common.audio_defaults import DEFAULT_SAMPLE_RATE
from dalston.common.models import validate_retention
from dalston.common.redis import get_redis as _get_redis
from dalston.common.timeouts import (
    WS_CLOSE_TIMEOUT,
    WS_OPEN_TIMEOUT,
    WS_PING_INTERVAL,
    WS_PING_TIMEOUT,
)
from dalston.common.utils import parse_session_id
from dalston.common.ws_close_codes import (
    WS_CLOSE_INVALID_REQUEST,
    WS_CLOSE_LAG_EXCEEDED,
    WS_CLOSE_RATE_LIMITED,
    WS_CLOSE_SERVICE_UNAVAILABLE,
)
from dalston.config import get_settings
from dalston.db.session import get_db as _get_db
from dalston.gateway.api.v1._realtime_common import (
    decrement_realtime_session_count as _decrement_session_count,
)
from dalston.gateway.api.v1._realtime_common import (
    get_realtime_auth_service as _get_auth_service,
)
from dalston.gateway.api.v1._realtime_common import (
    keep_session_alive as _keep_session_alive,
)
from dalston.gateway.api.v1._realtime_common import (
    resolve_rt_routing as _resolve_rt_routing,
)
from dalston.gateway.dependencies import get_session_router
from dalston.gateway.middleware.auth import authenticate_websocket
from dalston.gateway.services.auth import Scope
from dalston.gateway.services.rate_limiter import RedisRateLimiter
from dalston.gateway.services.realtime_sessions import RealtimeSessionService

logger = structlog.get_logger()


class RealtimeLagExceededError(RuntimeError):
    """Raised when worker closes a session due to lag budget exceedance."""


async def _check_realtime_rate_limits(
    websocket: WebSocket,
    tenant_id: UUID,
) -> bool:
    """Check rate limits for realtime WebSocket connections.

    Checks concurrent sessions limit before allowing the connection.
    Unlike REST endpoints which use Depends(), WebSocket handlers need
    to manually check rate limits.

    Args:
        websocket: The WebSocket connection (for sending error messages)
        tenant_id: Tenant UUID for rate limit lookup

    Returns:
        True if rate limits pass, False if exceeded (connection will be closed)
    """
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
                "code": "rate_limit_exceeded",
                "message": f"Concurrent session limit exceeded ({sessions_result.limit} max)",
            }
        )
        await websocket.close(code=WS_CLOSE_RATE_LIMITED, reason="Rate limit exceeded")
        return False

    await rate_limiter.increment_concurrent_sessions(tenant_id)
    return True


# Router for WebSocket endpoint (mounted under /audio/transcriptions)
stream_router = APIRouter(prefix="/audio/transcriptions", tags=["realtime"])

# Router for ElevenLabs-compatible endpoint (mounted under /speech-to-text)
elevenlabs_router = APIRouter(prefix="/speech-to-text", tags=["realtime", "elevenlabs"])


# -----------------------------------------------------------------------------
# WebSocket Endpoint
# -----------------------------------------------------------------------------


@stream_router.websocket("/stream")
async def realtime_transcription(
    websocket: WebSocket,
    language: Annotated[str, Query(description="Language code or 'auto'")] = "auto",
    model: Annotated[
        str,
        Query(
            description="Model name (e.g., 'faster-whisper-large-v3', 'parakeet-rnnt-0.6b'). Empty for any available worker."
        ),
    ] = "",
    encoding: Annotated[str, Query(description="Audio encoding")] = "pcm_s16le",
    sample_rate: Annotated[
        int, Query(description="Sample rate in Hz")
    ] = DEFAULT_SAMPLE_RATE,
    enable_vad: Annotated[bool, Query(description="Enable VAD events")] = True,
    interim_results: Annotated[
        bool, Query(description="Send partial transcripts")
    ] = True,
    word_timestamps: Annotated[bool, Query(description="Include word timing")] = False,
    vocabulary: Annotated[
        str | None,
        Query(
            description='JSON array of terms to boost recognition (e.g., \'["Dalston", "FastAPI"]\'). Max 100 terms.'
        ),
    ] = None,
    retention: Annotated[
        int | None,
        Query(
            description="Retention in days: 0 (transient), -1 (permanent), 1-3650 (days), or omit for server default"
        ),
    ] = None,
    resume_session_id: Annotated[
        str | None, Query(description="Link to previous session for resume")
    ] = None,
    # PII detection parameters (M26)
    pii_detection: Annotated[
        bool, Query(description="Enable PII detection on stored transcript")
    ] = False,
    pii_entity_types: Annotated[
        str | None,
        Query(description="Comma-separated PII entity types to detect"),
    ] = None,
    redact_pii_audio: Annotated[
        bool, Query(description="Generate redacted audio file")
    ] = False,
    pii_redaction_mode: Annotated[
        str, Query(description="Audio redaction mode: silence, beep")
    ] = "silence",
):
    """WebSocket endpoint for real-time streaming transcription.

    Protocol:
    - Client sends binary audio frames (PCM) or JSON control messages
    - Server sends JSON messages (session.begin, transcript.final, etc.)

    Query Parameters:
    - api_key: API key for authentication (required)
    - language: Language code or "auto" for detection
    - model: Model name (e.g., "faster-whisper-large-v3") or empty for any
    - encoding: Audio encoding (pcm_s16le, pcm_f32le, mulaw, alaw)
    - sample_rate: Audio sample rate (default: 16000)
    - enable_vad: Send vad.speech_start/end events
    - interim_results: Send transcript.partial messages
    - word_timestamps: Include word-level timing in results
    - vocabulary: JSON array of terms to boost recognition (max 100 terms, 50 chars each)
    - retention: Retention in days - 0 (transient), -1 (permanent), 1-3650 (days)
    - resume_session_id: Link to previous session for continuity
    - pii_detection: Enable PII detection on stored/enhanced transcript
    - pii_entity_types: Comma-separated entity types to detect
    - redact_pii_audio: Generate redacted audio file (requires retention != 0)
    - pii_redaction_mode: Audio redaction mode (silence, beep)
    """
    # Get session router via dependency (note: WebSocket endpoints can't use Depends
    # in the same way as REST endpoints, so we import directly)
    try:
        session_router = get_session_router()
    except Exception:
        await websocket.close(
            code=WS_CLOSE_SERVICE_UNAVAILABLE, reason="Service unavailable"
        )
        return

    # Authenticate BEFORE accepting the connection
    # This allows us to reject with proper close codes
    auth_service, db_gen = await _get_auth_service()
    try:
        api_key = await authenticate_websocket(
            websocket, auth_service, required_scope=Scope.REALTIME
        )
        if api_key is None:
            # Connection was closed with appropriate error code
            return
    finally:
        # Ensure the database generator is properly closed
        await db_gen.aclose()

    # Accept WebSocket connection after successful auth
    await websocket.accept()

    # Check rate limits (concurrent sessions)
    if not await _check_realtime_rate_limits(websocket, api_key.tenant_id):
        return

    # Track tenant_id for decrementing session count on disconnect
    session_tenant_id = api_key.tenant_id

    # Wrap everything after rate limit check in try/finally to ensure
    # the session counter is always decremented on any exit path
    allocation = None
    try:
        # Apply server default retention if not specified
        settings = get_settings()
        effective_retention = (
            retention if retention is not None else settings.retention_default_days
        )

        # Validate retention parameter (0=transient, -1=permanent, 1-3650=days)
        try:
            validate_retention(effective_retention)
        except ValueError as e:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "invalid_parameters",
                    "message": str(e),
                }
            )
            await websocket.close(
                code=WS_CLOSE_INVALID_REQUEST, reason="Invalid parameters"
            )
            return

        # Derive storage flags from retention for downstream use
        # 0 = transient (no storage)
        store_audio = effective_retention != 0
        store_transcript = effective_retention != 0

        # Validate and parse vocabulary parameter
        parsed_vocabulary: list[str] | None = None
        if vocabulary is not None:
            try:
                parsed_vocabulary = json.loads(vocabulary)
                if not isinstance(parsed_vocabulary, list):
                    raise ValueError("vocabulary must be a JSON array of strings")
                if len(parsed_vocabulary) > 100:
                    raise ValueError("vocabulary cannot exceed 100 terms")
                for term in parsed_vocabulary:
                    if not isinstance(term, str):
                        raise ValueError("vocabulary must contain only strings")
                    if len(term) > 50:
                        raise ValueError(
                            f"Each vocabulary term must be at most 50 characters, got {len(term)}"
                        )
                # Set to None if empty array
                if not parsed_vocabulary:
                    parsed_vocabulary = None
            except json.JSONDecodeError as e:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_parameters",
                        "message": f"Invalid JSON in vocabulary: {e}",
                    }
                )
                await websocket.close(
                    code=WS_CLOSE_INVALID_REQUEST, reason="Invalid parameters"
                )
                return
            except ValueError as e:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "invalid_parameters",
                        "message": str(e),
                    }
                )
                await websocket.close(
                    code=WS_CLOSE_INVALID_REQUEST, reason="Invalid parameters"
                )
                return

        # Validate: redact_pii_audio requires pii_detection and storage enabled
        if redact_pii_audio and not (pii_detection and store_audio):
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "invalid_parameters",
                    "message": "redact_pii_audio=true requires pii_detection=true and retention != 0.",
                }
            )
            await websocket.close(
                code=WS_CLOSE_INVALID_REQUEST, reason="Invalid parameters"
            )
            return

        rt = await _resolve_rt_routing(model if model else None)

        # Get client IP for logging
        client_ip = websocket.client.host if websocket.client else "unknown"

        # Acquire worker from Session Router (use alias for matching)
        allocation = await session_router.acquire_worker(
            language=language,
            model=rt.routing_model,
            client_ip=client_ip,
            runtime=rt.model_runtime,
            valid_runtimes=rt.valid_runtimes,
        )

        if allocation is None:
            # No capacity - send error and close
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "no_capacity",
                    "message": "No realtime workers available. Try again later.",
                }
            )
            await websocket.close(
                code=WS_CLOSE_SERVICE_UNAVAILABLE, reason="No capacity"
            )
            return

        log = logger.bind(
            session_id=allocation.session_id,
            instance=allocation.instance,
            client_ip=client_ip,
        )
        log.info("session_allocated")

        # Session lifecycle state
        db_session = None
        db_gen = None
        session_service = None
        session_error = None
        session_status = "completed"
        session_end_data = None
        keepalive_task = None

        try:
            # Start keepalive task to extend session TTL for long sessions
            keepalive_task = asyncio.create_task(
                _keep_session_alive(session_router, allocation.session_id)
            )
            # Bind session_id into structlog contextvars for downstream log calls
            structlog.contextvars.bind_contextvars(session_id=allocation.session_id)

            # Create session record in PostgreSQL for persistence/visibility
            previous_session_uuid = None

            if resume_session_id:
                try:
                    previous_session_uuid = parse_session_id(resume_session_id)
                except ValueError:
                    log.warning(
                        "invalid_resume_session_id", resume_session_id=resume_session_id
                    )

            try:
                # Get fresh DB session for persistence
                db_gen = _get_db()
                db_session = await db_gen.__anext__()
                settings = get_settings()
                session_service = RealtimeSessionService(db_session, settings)

                # Create session record
                # Store user's original model parameter (e.g., "fast", "parakeet-0.6b")
                # and the runtime that handled it (e.g., "parakeet", "faster-whisper")
                await session_service.create_session(
                    session_id=allocation.session_id,
                    tenant_id=api_key.tenant_id,
                    instance=allocation.instance,
                    client_ip=client_ip,
                    language=language,
                    model=model,
                    runtime=allocation.runtime,
                    encoding=encoding,
                    sample_rate=sample_rate,
                    retention=effective_retention,
                    previous_session_id=previous_session_uuid,
                    created_by_key_id=api_key.id,
                )
            except Exception as e:
                log.warning("session_db_create_failed", error=str(e))
                # Continue without persistence - session still works via Redis

            # Connect to worker and proxy bidirectionally
            session_end_data = await _proxy_to_worker(
                client_ws=websocket,
                worker_endpoint=allocation.endpoint,
                session_id=allocation.session_id,
                language=language,
                model=rt.effective_model,  # Pass selected model (auto-selected if empty)
                encoding=encoding,
                sample_rate=sample_rate,
                enable_vad=enable_vad,
                interim_results=interim_results,
                word_timestamps=word_timestamps,
                vocabulary=parsed_vocabulary,
                store_audio=store_audio,
                store_transcript=store_transcript,
            )
        except RealtimeLagExceededError:
            log.warning("session_lag_exceeded")
            session_error = "lag_exceeded"
            session_status = "error"
            session_end_data = None
        except WebSocketDisconnect:
            log.info("client_disconnected")
            session_status = "interrupted"
        except Exception as e:
            log.error("session_error", error=str(e))
            session_error = str(e)
            session_status = "error"
        finally:
            # Cancel keepalive task
            if keepalive_task:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

            # Release worker
            if allocation:
                await session_router.release_worker(allocation.session_id)
                log.info("session_released")

            # Update session stats from session.end message
            audio_uri = None
            transcript_uri = None
            if session_service and session_end_data:
                try:
                    # Debug: log the full session_end_data
                    log.debug("session_end_data_content", data=session_end_data)

                    # Extract stats from session.end message
                    audio_duration = session_end_data.get("total_audio_seconds", 0)
                    segments = session_end_data.get("segments", [])
                    transcript = session_end_data.get("transcript", "")
                    word_count = len(transcript.split()) if transcript else 0

                    # Extract storage URIs
                    audio_uri = session_end_data.get("audio_uri")
                    transcript_uri = session_end_data.get("transcript_uri")

                    log.info(
                        "session_stats_captured",
                        audio_duration=audio_duration,
                        segment_count=len(segments),
                        word_count=word_count,
                        audio_uri=audio_uri,
                        transcript_uri=transcript_uri,
                    )

                    await session_service.update_stats(
                        session_id=allocation.session_id,
                        audio_duration_seconds=audio_duration,
                        segment_count=len(segments),
                        word_count=word_count,
                    )
                except Exception as e:
                    log.warning("session_stats_update_failed", error=str(e))
            elif session_service:
                log.warning(
                    "session_end_data_missing",
                    msg="No session.end data received from worker",
                )
                if session_status == "completed":
                    session_status = "error"
                    session_error = session_error or "worker_no_session_end"

            # Finalize session in PostgreSQL
            if session_service:
                try:
                    await session_service.finalize_session(
                        session_id=allocation.session_id,
                        status=session_status,
                        error=session_error,
                        audio_uri=audio_uri,
                        transcript_uri=transcript_uri,
                    )
                except Exception as e:
                    log.warning("session_db_finalize_failed", error=str(e))

            # Close DB session
            if db_session:
                try:
                    await db_gen.aclose()
                except Exception:
                    pass

    finally:
        # Always decrement concurrent session count for rate limiting,
        # regardless of whether we successfully acquired a worker or hit
        # an early validation error. This ensures no counter leaks.
        await _decrement_session_count(session_tenant_id)


# -----------------------------------------------------------------------------
# ElevenLabs-Compatible WebSocket Endpoint
# -----------------------------------------------------------------------------


@elevenlabs_router.websocket("/realtime")
async def elevenlabs_realtime_transcription(
    websocket: WebSocket,
    language_code: Annotated[
        str, Query(description="Language code or 'auto'")
    ] = "auto",
    model_id: Annotated[
        str, Query(description="Model ID (scribe_v1, scribe_v2)")
    ] = "scribe_v1",
    audio_format: Annotated[
        str, Query(description="Audio format (pcm_16000, pcm_8000, etc.)")
    ] = "pcm_16000",
    commit_strategy: Annotated[
        str, Query(description="Commit strategy: 'vad' or 'manual'")
    ] = "vad",
    include_timestamps: Annotated[
        bool, Query(description="Include word-level timestamps")
    ] = False,
    keyterms: Annotated[
        str | None,
        Query(
            description="JSON array of bias terms to boost recognition "
            '(e.g., \'["PostgreSQL", "Kubernetes"]\'). Max 100 terms, 50 chars each.',
        ),
    ] = None,
):
    """ElevenLabs-compatible WebSocket endpoint for real-time transcription.

    This endpoint uses ElevenLabs protocol:
    - Client sends JSON messages with base64-encoded audio
    - Server sends ElevenLabs-format transcript messages

    Protocol:
    - Client sends: {"message_type": "input_audio_chunk", "audio_base_64": "...", ...}
    - Server sends: {"message_type": "partial_transcript", "text": "..."}
    - Server sends: {"message_type": "committed_transcript", "text": "..."}

    Query Parameters (ElevenLabs naming):
    - model_id: "scribe_v1" or "scribe_v2" (maps to parakeet-0.6b/1.1b)
    - language_code: ISO 639-1 code or "auto"
    - audio_format: "pcm_16000", "pcm_8000", etc.
    - commit_strategy: "vad" (auto) or "manual"
    - include_timestamps: Include word-level timing
    - keyterms: JSON array of terms to boost recognition
    """
    # Parse audio format (e.g., "pcm_16000" -> sample_rate=16000)
    sample_rate = DEFAULT_SAMPLE_RATE
    if audio_format.startswith("pcm_"):
        try:
            sample_rate = int(audio_format.split("_")[1])
        except (IndexError, ValueError):
            pass

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

    # Check rate limits (concurrent sessions)
    if not await _check_realtime_rate_limits(websocket, api_key.tenant_id):
        return

    # Track tenant_id for decrementing session count on disconnect
    session_tenant_id = api_key.tenant_id

    # Wrap everything after rate limit check in try/finally to ensure
    # the session counter is always decremented on any exit path
    allocation = None
    try:
        # Validate and parse keyterms parameter (ElevenLabs naming → vocabulary)
        parsed_vocabulary: list[str] | None = None
        if keyterms is not None:
            try:
                parsed_vocabulary = json.loads(keyterms)
                if not isinstance(parsed_vocabulary, list):
                    raise ValueError("keyterms must be a JSON array of strings")
                if len(parsed_vocabulary) > 100:
                    raise ValueError("keyterms cannot exceed 100 terms")
                for term in parsed_vocabulary:
                    if not isinstance(term, str):
                        raise ValueError("keyterms must contain only strings")
                    if len(term) > 50:
                        raise ValueError(
                            f"Each keyterm must be at most 50 characters, got {len(term)}"
                        )
                # Set to None if empty array
                if not parsed_vocabulary:
                    parsed_vocabulary = None
            except json.JSONDecodeError as e:
                await websocket.send_json(
                    {"message_type": "error", "error": f"Invalid JSON in keyterms: {e}"}
                )
                await websocket.close(
                    code=WS_CLOSE_INVALID_REQUEST, reason="Invalid parameters"
                )
                return
            except ValueError as e:
                await websocket.send_json({"message_type": "error", "error": str(e)})
                await websocket.close(
                    code=WS_CLOSE_INVALID_REQUEST, reason="Invalid parameters"
                )
                return

        # ElevenLabs model_id (scribe_v1, scribe_v2, etc.) is treated as "auto"
        rt = await _resolve_rt_routing(None)

        # Get client IP
        client_ip = websocket.client.host if websocket.client else "unknown"

        # Acquire worker
        allocation = await session_router.acquire_worker(
            language=language_code,
            model=rt.routing_model,
            client_ip=client_ip,
            runtime=rt.model_runtime,
            valid_runtimes=rt.valid_runtimes,
        )

        if allocation is None:
            await websocket.send_json(
                {"message_type": "error", "error": "No capacity available"}
            )
            await websocket.close(
                code=WS_CLOSE_SERVICE_UNAVAILABLE, reason="No capacity"
            )
            return

        log = logger.bind(
            session_id=allocation.session_id,
            instance=allocation.instance,
            client_ip=client_ip,
            protocol="elevenlabs",
        )
        log.info("elevenlabs_session_allocated")

        # Session lifecycle state
        keepalive_task = None
        try:
            # Start keepalive task to extend session TTL for long sessions
            keepalive_task = asyncio.create_task(
                _keep_session_alive(session_router, allocation.session_id)
            )

            # Send session_started message (ElevenLabs format)
            await websocket.send_json(
                {
                    "message_type": "session_started",
                    "session_id": allocation.session_id,
                    "config": {
                        "sample_rate": sample_rate,
                        "audio_format": audio_format,
                        "language_code": language_code,
                        "model_id": model_id,
                        "commit_strategy": commit_strategy,
                    },
                }
            )

            # Connect to worker with ElevenLabs protocol translation
            await _proxy_to_worker_elevenlabs(
                client_ws=websocket,
                worker_endpoint=allocation.endpoint,
                session_id=allocation.session_id,
                language=language_code,
                model=rt.effective_model,  # Auto-selected largest model from registry
                sample_rate=sample_rate,
                enable_vad=(commit_strategy == "vad"),
                interim_results=True,
                word_timestamps=include_timestamps,
                vocabulary=parsed_vocabulary,
            )
        except WebSocketDisconnect:
            log.info("elevenlabs_client_disconnected")
        except Exception as e:
            log.error("elevenlabs_session_error", error=str(e))
        finally:
            # Cancel keepalive task
            if keepalive_task:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

            if allocation:
                await session_router.release_worker(allocation.session_id)
                log.info("elevenlabs_session_released")

    finally:
        # Always decrement concurrent session count for rate limiting,
        # regardless of whether we successfully acquired a worker or hit
        # an early error. This ensures no counter leaks.
        await _decrement_session_count(session_tenant_id)


async def _proxy_to_worker(
    client_ws: WebSocket,
    worker_endpoint: str,
    session_id: str,
    language: str,
    model: str,
    encoding: str,
    sample_rate: int,
    enable_vad: bool,
    interim_results: bool,
    word_timestamps: bool,
    vocabulary: list[str] | None = None,
    store_audio: bool = False,
    store_transcript: bool = False,
) -> dict | None:
    """Proxy WebSocket connection between client and worker.

    Args:
        client_ws: Client WebSocket connection
        worker_endpoint: Worker WebSocket URL
        session_id: Session ID for the connection
        language: Language code
        model: Model variant
        encoding: Audio encoding
        sample_rate: Sample rate
        enable_vad: Enable VAD events
        interim_results: Enable interim results
        word_timestamps: Enable word timestamps
        vocabulary: List of terms to boost recognition

    Returns:
        Session end data with stats if received, None otherwise
    """
    from urllib.parse import urlencode

    import websockets

    # Build worker URL with session parameters (use urlencode for safe encoding)
    params: dict[str, str] = {
        "session_id": session_id,  # Pass Gateway's session_id to worker
        "language": language,
        "model": model,
        "encoding": encoding,
        "sample_rate": str(sample_rate),
        "enable_vad": str(enable_vad).lower(),
        "interim_results": str(interim_results).lower(),
        "word_timestamps": str(word_timestamps).lower(),
        "store_audio": str(store_audio).lower(),
        "store_transcript": str(store_transcript).lower(),
    }
    # Add vocabulary as JSON if provided
    if vocabulary:
        params["vocabulary"] = json.dumps(vocabulary)

    worker_url = f"{worker_endpoint}/session?{urlencode(params)}"

    # Connect with timeouts to prevent hanging connections
    async with websockets.connect(
        worker_url,
        open_timeout=WS_OPEN_TIMEOUT,
        close_timeout=WS_CLOSE_TIMEOUT,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
    ) as worker_ws:
        # Create tasks for bidirectional proxying
        client_to_worker = asyncio.create_task(
            _forward_client_to_worker(client_ws, worker_ws, session_id)
        )
        worker_to_client = asyncio.create_task(
            _forward_worker_to_client(worker_ws, client_ws, session_id)
        )

        # Wait for either direction to complete
        done, pending = await asyncio.wait(
            [client_to_worker, worker_to_client],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Log which task completed first for debugging
        first_done = (
            "client_to_worker" if client_to_worker in done else "worker_to_client"
        )
        logger.info(
            "proxy_task_completed", first_done=first_done, session_id=session_id
        )

        # If client_to_worker finished first, give worker time to send session.end
        # This handles the case where client disconnects but we still want stats
        # Worker may need to flush VAD buffer and do final transcription, so allow 10s
        session_end_data = None
        if client_to_worker in done and worker_to_client in pending:
            # Client disconnected/ended - wait for session.end from worker
            logger.info(
                "waiting_for_session_end",
                msg="Client finished, waiting for worker session.end",
            )
            try:
                await asyncio.wait_for(worker_to_client, timeout=10.0)
                session_end_data = worker_to_client.result()
                logger.info(
                    "session_end_received", has_data=session_end_data is not None
                )
            except TimeoutError:
                logger.warning(
                    "session_end_timeout", msg="Worker didn't send session.end in 10s"
                )
                worker_to_client.cancel()
                try:
                    await worker_to_client
                except asyncio.CancelledError:
                    pass
            except Exception as e:
                # Worker task failed
                logger.warning("session_end_error", error=str(e))
                pass
        else:
            # Worker finished first - this happens when worker closes connection
            logger.info("worker_finished_first", session_id=session_id)

            # Cancel remaining pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Get result from worker_to_client if it completed
            if worker_to_client in done:
                try:
                    session_end_data = worker_to_client.result()
                    logger.info(
                        "session_end_from_worker", has_data=session_end_data is not None
                    )
                except Exception as e:
                    logger.warning("session_end_result_error", error=str(e))

        # Check for exceptions in completed tasks
        # Don't re-raise client_to_worker exceptions if we got session data
        # (client disconnect is expected and we still want to return stats)
        for task in done:
            if task is not worker_to_client:  # Already handled above
                exc = task.exception()
                if exc is not None and session_end_data is None:
                    raise exc

        if (
            session_end_data is None
            and getattr(worker_ws, "close_code", None) == WS_CLOSE_LAG_EXCEEDED
        ):
            raise RealtimeLagExceededError("lag_exceeded")

        return session_end_data


async def _forward_client_to_worker(
    client_ws: WebSocket,
    worker_ws,
    session_id: str,
) -> None:
    """Forward messages from client to worker."""
    end_sent = False
    try:
        while True:
            # Receive from client (can be binary or text)
            message = await client_ws.receive()

            if message["type"] == "websocket.disconnect":
                # Client disconnected, send end to worker
                await worker_ws.send(json.dumps({"type": "end"}))
                end_sent = True
                break
            elif message["type"] == "websocket.receive":
                if "bytes" in message:
                    # Binary audio data
                    await worker_ws.send(message["bytes"])
                elif "text" in message:
                    # JSON control message - check if client sent "end"
                    await worker_ws.send(message["text"])
                    try:
                        data = json.loads(message["text"])
                        if data.get("type") == "end":
                            end_sent = True
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        logger.debug("client_to_worker_ended", session_id=session_id, error=str(e))
        # On abrupt disconnect, still try to send end to worker so it can finalize
        if not end_sent:
            try:
                await worker_ws.send(json.dumps({"type": "end"}))
                logger.debug("sent_end_after_disconnect", session_id=session_id)
            except Exception:
                # Worker connection might also be closed
                pass
        raise


async def _forward_worker_to_client(
    worker_ws,
    client_ws: WebSocket,
    session_id: str,
) -> dict | None:
    """Forward messages from worker to client.

    Returns:
        Session end data if received, None otherwise
    """
    session_end_data = None
    client_closed = False
    try:
        async for message in worker_ws:
            if isinstance(message, bytes):
                # Binary data (unusual for worker->client)
                if not client_closed:
                    try:
                        await client_ws.send_bytes(message)
                    except Exception:
                        client_closed = True
            else:
                # JSON message - capture session.end data before trying to forward
                try:
                    data = json.loads(message)
                    if data.get("type") == "session.end":
                        session_end_data = data
                except json.JSONDecodeError:
                    pass

                # Try to forward to client (may fail if client disconnected)
                if not client_closed:
                    try:
                        await client_ws.send_text(message)
                    except Exception:
                        client_closed = True
                        logger.debug(
                            "client_closed_during_forward",
                            session_id=session_id,
                        )

                # If we got session.end, we're done (whether or not client received it)
                if session_end_data is not None:
                    break
    except Exception as e:
        logger.debug("worker_to_client_ended", session_id=session_id, error=str(e))
        # Only raise if we don't have session data to return
        if session_end_data is None:
            raise
    return session_end_data


# -----------------------------------------------------------------------------
# ElevenLabs Protocol Translation
# -----------------------------------------------------------------------------


async def _proxy_to_worker_elevenlabs(
    client_ws: WebSocket,
    worker_endpoint: str,
    session_id: str,
    language: str,
    model: str,
    sample_rate: int,
    enable_vad: bool,
    interim_results: bool,
    word_timestamps: bool,
    vocabulary: list[str] | None = None,
) -> None:
    """Proxy with ElevenLabs protocol translation.

    Translates between:
    - ElevenLabs protocol (JSON with base64 audio)
    - Dalston native protocol (binary audio frames)
    """
    from urllib.parse import urlencode

    import websockets

    # Build worker URL
    params: dict[str, str] = {
        "session_id": session_id,
        "language": language,
        "model": model,
        "encoding": "pcm_s16le",
        "sample_rate": str(sample_rate),
        "enable_vad": str(enable_vad).lower(),
        "interim_results": str(interim_results).lower(),
        "word_timestamps": str(word_timestamps).lower(),
    }
    # Add vocabulary as JSON if provided
    if vocabulary:
        params["vocabulary"] = json.dumps(vocabulary)

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
            _elevenlabs_client_to_worker(client_ws, worker_ws, session_id)
        )
        worker_to_client = asyncio.create_task(
            _elevenlabs_worker_to_client(
                worker_ws, client_ws, session_id, word_timestamps
            )
        )

        done, pending = await asyncio.wait(
            [client_to_worker, worker_to_client],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc


async def _elevenlabs_client_to_worker(
    client_ws: WebSocket,
    worker_ws,
    session_id: str,
) -> None:
    """Translate ElevenLabs client messages to Dalston worker format.

    ElevenLabs sends:
        {"message_type": "input_audio_chunk", "audio_base_64": "...", "commit": false}

    Dalston expects:
        Binary audio frames
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
                    # Parse ElevenLabs JSON message
                    try:
                        data = json.loads(message["text"])
                        msg_type = data.get("message_type")

                        if msg_type == "input_audio_chunk":
                            # Decode base64 audio and send as binary
                            audio_b64 = data.get("audio_base_64", "")
                            if audio_b64:
                                audio_bytes = base64.b64decode(audio_b64)
                                await worker_ws.send(audio_bytes)

                            # Handle commit flag
                            if data.get("commit"):
                                await worker_ws.send(json.dumps({"type": "commit"}))

                        elif msg_type == "close_stream":
                            await worker_ws.send(json.dumps({"type": "end"}))
                            end_sent = True
                            break

                    except json.JSONDecodeError as e:
                        logger.debug(
                            "elevenlabs_json_parse_error",
                            session_id=session_id,
                            error=str(e),
                        )
                    except binascii.Error as e:
                        logger.debug(
                            "elevenlabs_base64_decode_error",
                            session_id=session_id,
                            error=str(e),
                        )
                    except KeyError as e:
                        logger.warning(
                            "elevenlabs_missing_field",
                            session_id=session_id,
                            error=str(e),
                        )
                elif "bytes" in message:
                    # Raw binary also accepted (passthrough)
                    await worker_ws.send(message["bytes"])

    except Exception as e:
        logger.debug(
            "elevenlabs_client_to_worker_ended",
            session_id=session_id,
            error=str(e),
        )
        # On abrupt disconnect, still try to send end to worker so it can finalize
        if not end_sent:
            try:
                await worker_ws.send(json.dumps({"type": "end"}))
                logger.debug(
                    "elevenlabs_sent_end_after_disconnect", session_id=session_id
                )
            except Exception:
                # Worker connection might also be closed
                pass
        raise


async def _elevenlabs_worker_to_client(
    worker_ws,
    client_ws: WebSocket,
    session_id: str,
    include_timestamps: bool,
) -> None:
    """Translate Dalston worker messages to ElevenLabs client format.

    Dalston sends:
        {"type": "transcript.partial", "text": "..."}
        {"type": "transcript.final", "text": "...", "words": [...]}
        {"type": "session.end", ...}

    ElevenLabs expects:
        {"message_type": "partial_transcript", "text": "..."}
        {"message_type": "committed_transcript", "text": "..."}
        {"message_type": "committed_transcript_with_timestamps", "text": "...", "words": [...]}
    """
    try:
        async for message in worker_ws:
            if isinstance(message, bytes):
                continue  # Skip binary messages

            try:
                data = json.loads(message)
                msg_type = data.get("type")
                translated = None

                if msg_type == "session.begin":
                    # Already sent session_started, skip
                    pass

                elif msg_type == "transcript.partial":
                    translated = {
                        "message_type": "partial_transcript",
                        "text": data.get("text", ""),
                    }

                elif msg_type == "transcript.final":
                    if include_timestamps and data.get("words"):
                        translated = {
                            "message_type": "committed_transcript_with_timestamps",
                            "text": data.get("text", ""),
                            "language_code": data.get("language", "en"),
                            "words": [
                                {
                                    "text": w.get("word", ""),
                                    "start": w.get("start", 0),
                                    "end": w.get("end", 0),
                                    "type": "word",
                                }
                                for w in data.get("words", [])
                            ],
                        }
                    else:
                        translated = {
                            "message_type": "committed_transcript",
                            "text": data.get("text", ""),
                        }

                elif msg_type == "vad.speech_start":
                    translated = {
                        "message_type": "speech_started",
                        "timestamp": data.get("timestamp", 0),
                    }

                elif msg_type == "vad.speech_end":
                    translated = {
                        "message_type": "speech_ended",
                        "timestamp": data.get("timestamp", 0),
                    }

                elif msg_type == "session.end":
                    translated = {
                        "message_type": "session_ended",
                        "total_audio_seconds": data.get("total_audio_seconds", 0),
                    }
                    await client_ws.send_json(translated)
                    break

                elif msg_type == "error":
                    translated = {
                        "message_type": "error",
                        "error": data.get("message", "Unknown error"),
                    }

                elif msg_type == "warning":
                    translated = {
                        "message_type": "warning",
                        "code": data.get("code", "warning"),
                        "message": data.get("message", ""),
                    }
                    if "lag_seconds" in data:
                        translated["lag_seconds"] = data.get("lag_seconds", 0)
                    if "warning_threshold_seconds" in data:
                        translated["warning_threshold_seconds"] = data.get(
                            "warning_threshold_seconds", 0
                        )
                    if "hard_threshold_seconds" in data:
                        translated["hard_threshold_seconds"] = data.get(
                            "hard_threshold_seconds", 0
                        )

                elif msg_type == "session.terminated":
                    translated = {
                        "message_type": "error",
                        "error": data.get("reason", "session_terminated"),
                    }

                if translated:
                    await client_ws.send_json(translated)

            except json.JSONDecodeError:
                pass

    except Exception as e:
        logger.debug(
            "elevenlabs_worker_to_client_ended",
            session_id=session_id,
            error=str(e),
        )
        raise
