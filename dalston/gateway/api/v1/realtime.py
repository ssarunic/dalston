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

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from dalston.common.events import publish_job_created
from dalston.common.models import resolve_model
from dalston.common.redis import get_redis as _get_redis
from dalston.common.utils import parse_session_id
from dalston.config import get_settings
from dalston.db.session import get_db as _get_db
from dalston.gateway.dependencies import get_session_router
from dalston.gateway.middleware.auth import authenticate_websocket
from dalston.gateway.services.auth import AuthService, Scope
from dalston.gateway.services.enhancement import EnhancementService
from dalston.gateway.services.realtime_sessions import RealtimeSessionService

logger = structlog.get_logger()


async def _get_auth_service() -> tuple[AuthService, any]:
    """Get AuthService for WebSocket authentication.

    Returns:
        Tuple of (AuthService, db_session) for use in WebSocket endpoints.
        Caller should ensure db_session lifecycle is managed properly.
    """
    redis = await _get_redis()
    db_gen = _get_db()
    db = await db_gen.__anext__()
    return AuthService(db, redis), db_gen


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
    store_audio: Annotated[bool, Query(description="Record audio to S3")] = False,
    store_transcript: Annotated[
        bool, Query(description="Save final transcript to S3")
    ] = False,
    resume_session_id: Annotated[
        str | None, Query(description="Link to previous session for resume")
    ] = None,
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
    - store_audio: Record audio to S3 during session
    - store_transcript: Save final transcript to S3 on end
    - resume_session_id: Link to previous session for continuity
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

    # Validate: enhance_on_end requires store_audio
    if enhance_on_end and not store_audio:
        await websocket.send_json(
            {
                "type": "error",
                "code": "invalid_parameters",
                "message": "enhance_on_end=true requires store_audio=true. "
                "Audio must be recorded to enable batch enhancement.",
            }
        )
        await websocket.close(code=4400, reason="Invalid parameters")
        return

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

    # Wrap everything after acquire_worker in try/finally to ensure cleanup
    db_session = None
    db_gen = None
    session_service = None
    session_error = None
    session_status = "completed"
    session_end_data = None

    try:
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
            # and the actual engine that handled it (e.g., "parakeet", "whisper")
            await session_service.create_session(
                session_id=allocation.session_id,
                tenant_id=api_key.tenant_id,
                worker_id=allocation.worker_id,
                client_ip=client_ip,
                language=language,
                model=model,
                engine=allocation.engine,
                encoding=encoding,
                sample_rate=sample_rate,
                store_audio=store_audio,
                store_transcript=store_transcript,
                enhance_on_end=enhance_on_end,
                previous_session_id=previous_session_uuid,
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
            model=resolved_model,
            encoding=encoding,
            sample_rate=sample_rate,
            enable_vad=enable_vad,
            interim_results=interim_results,
            word_timestamps=word_timestamps,
            store_audio=store_audio,
            store_transcript=store_transcript,
        )
    except WebSocketDisconnect:
        log.info("client_disconnected")
        session_status = "interrupted"
    except Exception as e:
        log.error("session_error", error=str(e))
        session_error = str(e)
        session_status = "error"
    finally:
        # Release worker
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
                audio_duration = session_end_data.get("total_duration", 0)
                segments = session_end_data.get("segments", [])
                transcript = session_end_data.get("transcript", "")
                word_count = len(transcript.split()) if transcript else 0

                # Extract storage URIs
                audio_uri = session_end_data.get("audio_uri")
                transcript_uri = session_end_data.get("transcript_uri")

                log.info(
                    "session_stats_captured",
                    audio_duration=audio_duration,
                    utterance_count=len(segments),
                    word_count=word_count,
                    audio_uri=audio_uri,
                    transcript_uri=transcript_uri,
                )

                await session_service.update_stats(
                    session_id=allocation.session_id,
                    audio_duration_seconds=audio_duration,
                    utterance_count=len(segments),
                    word_count=word_count,
                )
            except Exception as e:
                log.warning("session_stats_update_failed", error=str(e))
        elif session_service:
            log.warning(
                "session_end_data_missing",
                msg="No session.end data received from worker",
            )

        # Create enhancement job if requested (M07 Hybrid Mode)
        enhancement_job_id = None
        if (
            session_service
            and enhance_on_end
            and session_status == "completed"
            and audio_uri
        ):
            try:
                enhancement_service = EnhancementService(db_session, get_settings())

                # Get the session from DB to pass to enhancement service
                session_record = await session_service.get_session(
                    allocation.session_id
                )
                if session_record:
                    enhancement_job = (
                        await enhancement_service.create_enhancement_job_with_audio(
                            session=session_record,
                            audio_uri=audio_uri,
                            enhance_diarization=True,
                            enhance_word_timestamps=True,
                            # TODO: Read these from session parameters when we add them
                            enhance_llm_cleanup=False,
                            enhance_emotions=False,
                        )
                    )
                    enhancement_job_id = enhancement_job.id

                    # Publish event for orchestrator to pick up the job
                    redis_client = await _get_redis()
                    await publish_job_created(redis_client, enhancement_job.id)

                    log.info(
                        "enhancement_job_created",
                        enhancement_job_id=str(enhancement_job_id),
                    )
            except Exception as e:
                log.warning("enhancement_job_creation_failed", error=str(e))
                # Don't fail the session if enhancement fails

        # Finalize session in PostgreSQL
        if session_service:
            try:
                await session_service.finalize_session(
                    session_id=allocation.session_id,
                    status=session_status,
                    error=session_error,
                    audio_uri=audio_uri,
                    transcript_uri=transcript_uri,
                    enhancement_job_id=enhancement_job_id,
                )
            except Exception as e:
                log.warning("session_db_finalize_failed", error=str(e))

        # Close DB session
        if db_session:
            try:
                await db_gen.aclose()
            except Exception:
                pass


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

    # Wrap everything after acquire_worker in try/finally to ensure cleanup
    try:
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

    Returns:
        Session end data with stats if received, None otherwise
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
            "store_audio": str(store_audio).lower(),
            "store_transcript": str(store_transcript).lower(),
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
