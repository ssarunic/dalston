"""Real-time transcription WebSocket endpoint.

WS /v1/audio/transcriptions/stream - Streaming transcription
GET /v1/realtime/status - System capacity
GET /v1/realtime/workers - List workers
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from dalston.gateway.dependencies import (
    RequireJobsRead,
    get_auth_service,
    get_session_router,
)
from dalston.gateway.middleware.auth import authenticate_websocket
from dalston.gateway.services.auth import Scope
from dalston.session_router import SessionRouter

logger = structlog.get_logger()

# Router for WebSocket endpoint (mounted under /audio/transcriptions)
stream_router = APIRouter(prefix="/audio/transcriptions", tags=["realtime"])

# Router for management endpoints (mounted under /realtime)
management_router = APIRouter(prefix="/realtime", tags=["realtime"])


# -----------------------------------------------------------------------------
# WebSocket Endpoint
# -----------------------------------------------------------------------------


@stream_router.websocket("/stream")
async def realtime_transcription(
    websocket: WebSocket,
    language: Annotated[str, Query(description="Language code or 'auto'")] = "auto",
    model: Annotated[str, Query(description="Model: 'fast' or 'accurate'")] = "fast",
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

    redis = await _get_redis()
    auth_service = await get_auth_service(redis)

    api_key = await authenticate_websocket(
        websocket, auth_service, required_scope=Scope.REALTIME
    )
    if api_key is None:
        # Connection was closed with appropriate error code
        return

    # Accept WebSocket connection after successful auth
    await websocket.accept()

    # Get client IP for logging
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Acquire worker from Session Router
    allocation = await session_router.acquire_worker(
        language=language,
        model=model,
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
            model=model,
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
