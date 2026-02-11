"""Real-time transcription WebSocket endpoint.

WS /v1/audio/transcriptions/stream - Streaming transcription (Dalston native)
WS /v1/speech-to-text/realtime - Streaming transcription (ElevenLabs compatible)
GET /v1/realtime/status - System capacity
GET /v1/realtime/workers - List workers
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from dalston.common.models import resolve_model
from dalston.gateway.dependencies import (
    RequireJobsRead,
    get_session_router,
)
from dalston.gateway.middleware.auth import authenticate_websocket
from dalston.gateway.services.auth import Scope
from dalston.session_router import SessionRouter

logger = structlog.get_logger()

# Router for WebSocket endpoint (mounted under /audio/transcriptions)
stream_router = APIRouter(prefix="/audio/transcriptions", tags=["realtime"])

# Router for ElevenLabs-compatible endpoint (mounted under /speech-to-text)
elevenlabs_router = APIRouter(prefix="/speech-to-text", tags=["realtime", "elevenlabs"])

# Router for management endpoints (mounted under /realtime)
management_router = APIRouter(prefix="/realtime", tags=["realtime"])


# -----------------------------------------------------------------------------
# WebSocket Endpoint
# -----------------------------------------------------------------------------


@stream_router.websocket("/stream")
async def realtime_transcription(
    websocket: WebSocket,
    language: Annotated[str, Query(description="Language code or 'auto'")] = "auto",
    model: Annotated[
        str,
        Query(description="Model ID or alias (e.g., whisper-large-v3, fast, accurate)"),
    ] = "fast",
    encoding: Annotated[str, Query(description="Audio encoding")] = "pcm_s16le",
    sample_rate: Annotated[int, Query(description="Sample rate in Hz")] = 16000,
    enable_vad: Annotated[bool, Query(description="Enable VAD events")] = True,
    interim_results: Annotated[
        bool, Query(description="Send partial transcripts")
    ] = True,
    word_timestamps: Annotated[bool, Query(description="Include word timing")] = False,
    enhance_on_end: Annotated[
        bool, Query(description="Trigger batch enhancement")
    ] = False,
):
    """WebSocket endpoint for real-time streaming transcription.

    Protocol:
    - Client sends binary audio frames (PCM) or JSON control messages
    - Server sends JSON messages (session.begin, transcript.final, etc.)

    Query Parameters:
    - api_key: API key for authentication (required)
    - language: Language code or "auto" for detection
    - model: "fast" (distil-whisper) or "accurate" (large-v3)
    - encoding: Audio encoding (pcm_s16le, pcm_f32le, mulaw, alaw)
    - sample_rate: Audio sample rate (default: 16000)
    - enable_vad: Send vad.speech_start/end events
    - interim_results: Send transcript.partial messages
    - word_timestamps: Include word-level timing in results
    - enhance_on_end: Trigger batch enhancement when session ends
    """
    # Get session router via dependency (note: WebSocket endpoints can't use Depends
    # in the same way as REST endpoints, so we import directly)
    try:
        session_router = get_session_router()
    except Exception:
        await websocket.close(code=4503, reason="Service unavailable")
        return

    # Authenticate BEFORE accepting the connection
    # This allows us to reject with proper close codes
    # Use the same dependency pattern as REST endpoints
    from dalston.common.redis import get_redis as _get_redis
    from dalston.db.session import get_db as _get_db
    from dalston.gateway.services.auth import AuthService

    redis = await _get_redis()
    async for db in _get_db():
        auth_service = AuthService(db, redis)
        break

    api_key = await authenticate_websocket(
        websocket, auth_service, required_scope=Scope.REALTIME
    )
    if api_key is None:
        # Connection was closed with appropriate error code
        return

    # Accept WebSocket connection after successful auth
    await websocket.accept()

    # Validate model parameter using model registry
    # For realtime, we keep the original alias (fast/accurate) for worker routing
    # Workers advertise aliases, not fully resolved model IDs
    try:
        model_def = resolve_model(model)
        # Keep original alias for routing, resolved ID for logging
        routing_model = model  # Use alias (fast, accurate, parakeet)
        resolved_model = model_def.id  # For logging
    except ValueError as e:
        await websocket.send_json(
            {
                "type": "error",
                "code": "invalid_model",
                "message": str(e),
            }
        )
        await websocket.close(code=4400, reason="Invalid model")
        return

    # Get client IP for logging
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Acquire worker from Session Router (use alias for matching)
    allocation = await session_router.acquire_worker(
        language=language,
        model=routing_model,
        client_ip=client_ip,
        enhance_on_end=enhance_on_end,
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
        await websocket.close(code=4503, reason="No capacity")
        return

    log = logger.bind(
        session_id=allocation.session_id,
        worker_id=allocation.worker_id,
        client_ip=client_ip,
    )
    log.info("session_allocated")

    # Bind session_id into structlog contextvars for downstream log calls
    structlog.contextvars.bind_contextvars(session_id=allocation.session_id)

    # Connect to worker and proxy bidirectionally
    try:
        await _proxy_to_worker(
            client_ws=websocket,
            worker_endpoint=allocation.endpoint,
            session_id=allocation.session_id,
            language=language,
            model=resolved_model,
            encoding=encoding,
            sample_rate=sample_rate,
            enable_vad=enable_vad,
            interim_results=interim_results,
            word_timestamps=word_timestamps,
        )
    except WebSocketDisconnect:
        log.info("client_disconnected")
    except Exception as e:
        log.error("session_error", error=str(e))
    finally:
        # Release worker
        await session_router.release_worker(allocation.session_id)
        log.info("session_released")


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
    """
    # Parse audio format (e.g., "pcm_16000" -> sample_rate=16000)
    sample_rate = 16000
    if audio_format.startswith("pcm_"):
        try:
            sample_rate = int(audio_format.split("_")[1])
        except (IndexError, ValueError):
            pass

    # Get session router
    try:
        session_router = get_session_router()
    except Exception:
        await websocket.close(code=4503, reason="Service unavailable")
        return

    # Authenticate
    from dalston.common.redis import get_redis as _get_redis
    from dalston.db.session import get_db as _get_db
    from dalston.gateway.services.auth import AuthService

    redis = await _get_redis()
    async for db in _get_db():
        auth_service = AuthService(db, redis)
        break

    api_key = await authenticate_websocket(
        websocket, auth_service, required_scope=Scope.REALTIME
    )
    if api_key is None:
        return

    # Accept connection
    await websocket.accept()

    # Validate and resolve model
    # For ElevenLabs, scribe_v1/v2 map to parakeet models
    # Workers advertise "parakeet" as a capability, so use that for routing
    try:
        model_def = resolve_model(model_id)
        resolved_model = model_def.id
        # For parakeet models, use "parakeet" alias for routing
        if resolved_model.startswith("parakeet-"):
            routing_model = "parakeet"
        else:
            routing_model = model_id
    except ValueError as e:
        await websocket.send_json({"message_type": "error", "error": str(e)})
        await websocket.close(code=4400, reason="Invalid model")
        return

    # Get client IP
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Acquire worker
    allocation = await session_router.acquire_worker(
        language=language_code,
        model=routing_model,
        client_ip=client_ip,
        enhance_on_end=False,
    )

    if allocation is None:
        await websocket.send_json(
            {"message_type": "error", "error": "No capacity available"}
        )
        await websocket.close(code=4503, reason="No capacity")
        return

    log = logger.bind(
        session_id=allocation.session_id,
        worker_id=allocation.worker_id,
        client_ip=client_ip,
        protocol="elevenlabs",
    )
    log.info("elevenlabs_session_allocated")

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
    try:
        await _proxy_to_worker_elevenlabs(
            client_ws=websocket,
            worker_endpoint=allocation.endpoint,
            session_id=allocation.session_id,
            language=language_code,
            model=resolved_model,
            sample_rate=sample_rate,
            enable_vad=(commit_strategy == "vad"),
            interim_results=True,
            word_timestamps=include_timestamps,
        )
    except WebSocketDisconnect:
        log.info("elevenlabs_client_disconnected")
    except Exception as e:
        log.error("elevenlabs_session_error", error=str(e))
    finally:
        await session_router.release_worker(allocation.session_id)
        log.info("elevenlabs_session_released")


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
) -> None:
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
    """
    from urllib.parse import urlencode

    import websockets

    # Build worker URL with session parameters (use urlencode for safe encoding)
    params = urlencode(
        {
            "session_id": session_id,  # Pass Gateway's session_id to worker
            "language": language,
            "model": model,
            "encoding": encoding,
            "sample_rate": sample_rate,
            "enable_vad": str(enable_vad).lower(),
            "interim_results": str(interim_results).lower(),
            "word_timestamps": str(word_timestamps).lower(),
        }
    )
    worker_url = f"{worker_endpoint}/session?{params}"

    # Connect with timeouts to prevent hanging connections
    async with websockets.connect(
        worker_url,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
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

        # Cancel pending tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Re-raise any exceptions from completed tasks
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc


async def _forward_client_to_worker(
    client_ws: WebSocket,
    worker_ws,
    session_id: str,
) -> None:
    """Forward messages from client to worker."""
    try:
        while True:
            # Receive from client (can be binary or text)
            message = await client_ws.receive()

            if message["type"] == "websocket.disconnect":
                # Client disconnected, send end to worker
                await worker_ws.send(json.dumps({"type": "end"}))
                break
            elif message["type"] == "websocket.receive":
                if "bytes" in message:
                    # Binary audio data
                    await worker_ws.send(message["bytes"])
                elif "text" in message:
                    # JSON control message
                    await worker_ws.send(message["text"])
    except Exception as e:
        logger.debug("client_to_worker_ended", session_id=session_id, error=str(e))
        raise


async def _forward_worker_to_client(
    worker_ws,
    client_ws: WebSocket,
    session_id: str,
) -> None:
    """Forward messages from worker to client."""
    try:
        async for message in worker_ws:
            if isinstance(message, bytes):
                # Binary data (unusual for worker->client)
                await client_ws.send_bytes(message)
            else:
                # JSON message
                await client_ws.send_text(message)

                # Check if session ended
                try:
                    data = json.loads(message)
                    if data.get("type") == "session.end":
                        break
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.debug("worker_to_client_ended", session_id=session_id, error=str(e))
        raise


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
) -> None:
    """Proxy with ElevenLabs protocol translation.

    Translates between:
    - ElevenLabs protocol (JSON with base64 audio)
    - Dalston native protocol (binary audio frames)
    """
    from urllib.parse import urlencode

    import websockets

    # Build worker URL
    params = urlencode(
        {
            "session_id": session_id,
            "language": language,
            "model": model,
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate,
            "enable_vad": str(enable_vad).lower(),
            "interim_results": str(interim_results).lower(),
            "word_timestamps": str(word_timestamps).lower(),
        }
    )
    worker_url = f"{worker_endpoint}/session?{params}"

    async with websockets.connect(
        worker_url,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
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
    try:
        while True:
            message = await client_ws.receive()

            if message["type"] == "websocket.disconnect":
                await worker_ws.send(json.dumps({"type": "end"}))
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
                            break

                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug(
                            "elevenlabs_parse_error",
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


# -----------------------------------------------------------------------------
# Management Endpoints
# -----------------------------------------------------------------------------


class RealtimeStatusResponse(BaseModel):
    """Real-time system status."""

    status: str
    total_capacity: int
    active_sessions: int
    available_capacity: int
    worker_count: int
    ready_workers: int


class WorkerStatusResponse(BaseModel):
    """Worker status."""

    worker_id: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models: list[str]
    languages: list[str]


class WorkersListResponse(BaseModel):
    """List of workers."""

    workers: list[WorkerStatusResponse]
    total: int


@management_router.get(
    "/status",
    response_model=RealtimeStatusResponse,
    summary="Get realtime system status",
    description="Get capacity and availability information for real-time transcription.",
)
async def get_realtime_status(
    api_key: RequireJobsRead,
    router: SessionRouter = Depends(get_session_router),
) -> RealtimeStatusResponse:
    """Get real-time transcription system status."""
    capacity = await router.get_capacity()

    # Determine overall status
    if capacity.ready_workers == 0:
        status = "unavailable"
    elif capacity.available_capacity == 0:
        status = "at_capacity"
    else:
        status = "ready"

    return RealtimeStatusResponse(
        status=status,
        total_capacity=capacity.total_capacity,
        active_sessions=capacity.used_capacity,
        available_capacity=capacity.available_capacity,
        worker_count=capacity.worker_count,
        ready_workers=capacity.ready_workers,
    )


@management_router.get(
    "/workers",
    response_model=WorkersListResponse,
    summary="List realtime workers",
    description="List all registered real-time transcription workers.",
)
async def list_realtime_workers(
    api_key: RequireJobsRead,
    router: SessionRouter = Depends(get_session_router),
) -> WorkersListResponse:
    """List all real-time workers."""
    workers = await router.list_workers()

    return WorkersListResponse(
        workers=[
            WorkerStatusResponse(
                worker_id=w.worker_id,
                endpoint=w.endpoint,
                status=w.status,
                capacity=w.capacity,
                active_sessions=w.active_sessions,
                models=w.models,
                languages=w.languages,
            )
            for w in workers
        ],
        total=len(workers),
    )


@management_router.get(
    "/workers/{worker_id}",
    response_model=WorkerStatusResponse,
    summary="Get worker status",
    description="Get status of a specific real-time worker.",
    responses={404: {"description": "Worker not found"}},
)
async def get_worker_status(
    worker_id: str,
    api_key: RequireJobsRead,
    router: SessionRouter = Depends(get_session_router),
) -> WorkerStatusResponse:
    """Get specific worker status."""
    from fastapi import HTTPException

    worker = await router.get_worker(worker_id)

    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")

    return WorkerStatusResponse(
        worker_id=worker.worker_id,
        endpoint=worker.endpoint,
        status=worker.status,
        capacity=worker.capacity,
        active_sessions=worker.active_sessions,
        models=worker.models,
        languages=worker.languages,
    )
