"""Real-time session history and enhancement endpoints.

GET /v1/realtime/sessions - List sessions
GET /v1/realtime/sessions/{session_id} - Get session details
DELETE /v1/realtime/sessions/{session_id} - Delete session
GET /v1/realtime/sessions/{session_id}/transcript - Get transcript
GET /v1/realtime/sessions/{session_id}/audio - Get audio URL
GET /v1/realtime/sessions/{session_id}/enhancement - Get enhancement status
POST /v1/realtime/sessions/{session_id}/enhance - Trigger enhancement
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.events import publish_job_created
from dalston.common.redis import get_redis as _get_redis
from dalston.common.s3 import get_s3_client
from dalston.config import get_settings
from dalston.db.session import get_db
from dalston.gateway.dependencies import RequireJobsRead
from dalston.gateway.services.enhancement import EnhancementError, EnhancementService
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.realtime_sessions import RealtimeSessionService
from dalston.gateway.services.storage import StorageService

logger = structlog.get_logger()

router = APIRouter(prefix="/realtime", tags=["realtime"])

# Presigned URL expiry time in seconds (1 hour)
PRESIGNED_URL_EXPIRY_SECONDS = 3600


# -----------------------------------------------------------------------------
# Response Models
# -----------------------------------------------------------------------------


class SessionSummary(BaseModel):
    """Session summary for list view."""

    id: str
    status: str
    language: str | None
    model: str | None
    engine: str | None
    audio_duration_seconds: float
    segment_count: int
    word_count: int
    store_audio: bool
    store_transcript: bool
    started_at: str
    ended_at: str | None


class SessionDetail(BaseModel):
    """Full session details."""

    id: str
    status: str
    language: str | None
    model: str | None
    engine: str | None
    encoding: str | None
    sample_rate: int | None
    audio_duration_seconds: float
    segment_count: int
    word_count: int
    store_audio: bool
    store_transcript: bool
    enhance_on_end: bool
    audio_uri: str | None
    transcript_uri: str | None
    enhancement_job_id: str | None
    worker_id: str | None
    client_ip: str | None
    previous_session_id: str | None
    started_at: str
    ended_at: str | None
    error: str | None


class SessionsListResponse(BaseModel):
    """List of sessions."""

    sessions: list[SessionSummary]
    cursor: str | None
    has_more: bool


class EnhancementStatusResponse(BaseModel):
    """Enhancement job status for a session."""

    session_id: str
    status: str  # not_requested, pending, processing, completed, failed
    enhancement_job_id: str | None = None
    job_status: str | None = None
    transcript: dict | None = None
    error: str | None = None


# -----------------------------------------------------------------------------
# Session History Endpoints
# -----------------------------------------------------------------------------


@router.get(
    "/sessions",
    response_model=SessionsListResponse,
    summary="List realtime sessions",
    description="List past and active realtime transcription sessions.",
)
async def list_realtime_sessions(
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str | None, Query(description="Filter by status")] = None,
    since: Annotated[
        str | None, Query(description="Sessions started after (ISO 8601)")
    ] = None,
    until: Annotated[
        str | None, Query(description="Sessions started before (ISO 8601)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 50,
    cursor: Annotated[
        str | None, Query(description="Pagination cursor from previous response")
    ] = None,
    sort: Annotated[
        Literal["started_desc", "started_asc"],
        Query(description="Sort order by started timestamp"),
    ] = "started_desc",
) -> SessionsListResponse:
    """List realtime sessions for the authenticated tenant."""
    # Parse datetime filters
    since_dt = None
    until_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid 'since' datetime format"
            ) from None
    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid 'until' datetime format"
            ) from None

    settings = get_settings()
    service = RealtimeSessionService(db, settings)

    sessions, has_more = await service.list_sessions(
        tenant_id=api_key.tenant_id,
        status=status,
        since=since_dt,
        until=until_dt,
        limit=limit,
        cursor=cursor,
        sort=sort,
    )

    # Compute next cursor from last session
    next_cursor = (
        service.encode_session_cursor(sessions[-1]) if sessions and has_more else None
    )

    return SessionsListResponse(
        sessions=[
            SessionSummary(
                id=str(s.id),
                status=s.status,
                language=s.language,
                model=s.model,
                engine=s.engine,
                audio_duration_seconds=s.audio_duration_seconds,
                segment_count=s.segment_count,
                word_count=s.word_count,
                store_audio=s.store_audio,
                store_transcript=s.store_transcript,
                started_at=s.started_at.isoformat(),
                ended_at=s.ended_at.isoformat() if s.ended_at else None,
            )
            for s in sessions
        ],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetail,
    summary="Get session details",
    description="Get full details of a realtime session.",
    responses={404: {"description": "Session not found"}},
)
async def get_realtime_session(
    session_id: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionDetail:
    """Get details of a specific realtime session."""
    settings = get_settings()
    service = RealtimeSessionService(db, settings)

    session = await service.get_session(session_id)

    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify tenant access
    if session.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionDetail(
        id=str(session.id),
        status=session.status,
        language=session.language,
        model=session.model,
        engine=session.engine,
        encoding=session.encoding,
        sample_rate=session.sample_rate,
        audio_duration_seconds=session.audio_duration_seconds,
        segment_count=session.segment_count,
        word_count=session.word_count,
        store_audio=session.store_audio,
        store_transcript=session.store_transcript,
        enhance_on_end=session.enhance_on_end,
        audio_uri=session.audio_uri,
        transcript_uri=session.transcript_uri,
        enhancement_job_id=str(session.enhancement_job_id)
        if session.enhancement_job_id
        else None,
        worker_id=session.worker_id,
        client_ip=session.client_ip,
        previous_session_id=str(session.previous_session_id)
        if session.previous_session_id
        else None,
        started_at=session.started_at.isoformat(),
        ended_at=session.ended_at.isoformat() if session.ended_at else None,
        error=session.error,
    )


@router.delete(
    "/sessions/{session_id}",
    summary="Delete a session",
    description="Delete a realtime session. Only completed, error, or interrupted sessions can be deleted.",
    responses={
        404: {"description": "Session not found"},
        409: {"description": "Cannot delete active session"},
    },
)
async def delete_realtime_session(
    session_id: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete a realtime session.

    Only non-active sessions (completed, error, interrupted) can be deleted.
    """
    settings = get_settings()
    service = RealtimeSessionService(db, settings)

    try:
        deleted = await service.delete_session(session_id, api_key.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"deleted": True, "session_id": session_id}


@router.get(
    "/sessions/{session_id}/transcript",
    summary="Get session transcript",
    description="Download the transcript for a session (if stored).",
    responses={
        404: {"description": "Session or transcript not found"},
    },
)
async def get_session_transcript(
    session_id: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get transcript JSON for a session."""
    settings = get_settings()
    service = RealtimeSessionService(db, settings)

    session = await service.get_session(session_id)

    if session is None or session.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.transcript_uri:
        raise HTTPException(status_code=404, detail="Transcript not available")

    # Parse S3 URI and fetch transcript
    # Format: s3://bucket/key
    uri_parts = session.transcript_uri.replace("s3://", "").split("/", 1)
    bucket = uri_parts[0]
    key = uri_parts[1] if len(uri_parts) > 1 else ""

    async with get_s3_client(settings) as s3:
        try:
            response = await s3.get_object(Bucket=bucket, Key=key)
            body = await response["Body"].read()
            transcript = json.loads(body.decode("utf-8"))
            return JSONResponse(content=transcript)
        except Exception as e:
            logger.warning(
                "transcript_fetch_failed",
                session_id=session_id,
                bucket=bucket,
                key=key,
                error=str(e),
            )
            raise HTTPException(
                status_code=404, detail="Transcript not found"
            ) from None


def _normalize_realtime_transcript(transcript: dict) -> dict:
    """Normalize realtime transcript to batch format for export.

    Realtime transcripts have 'utterances' while batch transcripts have 'segments'.
    This function converts the realtime format to match what ExportService expects.
    """
    utterances = transcript.get("utterances", [])
    return {
        "text": transcript.get("text", ""),
        "segments": [
            {
                "id": str(utt.get("id", idx)),
                "start": utt.get("start", 0.0),
                "end": utt.get("end", 0.0),
                "text": utt.get("text", ""),
                "speaker_id": None,  # Realtime doesn't have speaker diarization
            }
            for idx, utt in enumerate(utterances)
        ],
        "words": [],  # Realtime doesn't have word-level data
    }


@router.get(
    "/sessions/{session_id}/export/{format}",
    summary="Export session transcript",
    description="Export realtime session transcript in specified format: srt, vtt, txt, json",
    responses={
        200: {
            "description": "Exported transcript",
            "content": {
                "text/plain": {"schema": {"type": "string"}},
                "text/vtt": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "object"}},
            },
        },
        400: {"description": "Unsupported format"},
        404: {"description": "Session or transcript not found"},
    },
)
async def export_session_transcript(
    session_id: str,
    format: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_speakers: Annotated[
        bool, Query(description="Include speaker labels in output")
    ] = True,
    max_line_length: Annotated[
        int, Query(ge=10, le=200, description="Max characters per subtitle line")
    ] = 42,
    max_lines: Annotated[
        int, Query(ge=1, le=10, description="Max lines per subtitle block")
    ] = 2,
) -> Response:
    """Export realtime session transcript in specified format.

    Supported formats: srt, vtt, txt, json
    """
    export_service = ExportService()

    # Validate format
    export_format = export_service.validate_format(format)

    settings = get_settings()
    service = RealtimeSessionService(db, settings)

    # Get session and verify tenant access
    session = await service.get_session(session_id)

    if session is None or session.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.transcript_uri:
        raise HTTPException(status_code=404, detail="Transcript not available")

    # Parse S3 URI and fetch transcript
    uri_parts = session.transcript_uri.replace("s3://", "").split("/", 1)
    bucket = uri_parts[0]
    key = uri_parts[1] if len(uri_parts) > 1 else ""

    async with get_s3_client(settings) as s3:
        try:
            response = await s3.get_object(Bucket=bucket, Key=key)
            body = await response["Body"].read()
            transcript = json.loads(body.decode("utf-8"))
        except Exception as e:
            logger.warning(
                "transcript_fetch_failed",
                session_id=session_id,
                bucket=bucket,
                key=key,
                error=str(e),
            )
            raise HTTPException(
                status_code=404, detail="Transcript not found"
            ) from None

    # Normalize realtime transcript to batch format for export
    normalized_transcript = _normalize_realtime_transcript(transcript)

    return export_service.create_export_response(
        transcript=normalized_transcript,
        export_format=export_format,
        include_speakers=include_speakers,
        max_line_length=max_line_length,
        max_lines=max_lines,
    )


@router.get(
    "/sessions/{session_id}/audio",
    summary="Get session audio URL",
    description="Get a presigned URL to download the audio for a session (if stored).",
    responses={
        404: {"description": "Session or audio not found"},
    },
)
async def get_session_audio(
    session_id: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get presigned URL for session audio."""
    settings = get_settings()
    service = RealtimeSessionService(db, settings)

    session = await service.get_session(session_id)

    if session is None or session.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.audio_uri:
        raise HTTPException(status_code=404, detail="Audio not available")

    # Parse S3 URI and generate presigned URL
    uri_parts = session.audio_uri.replace("s3://", "").split("/", 1)
    bucket = uri_parts[0]
    key = uri_parts[1] if len(uri_parts) > 1 else ""

    storage = StorageService(settings)
    try:
        url = await storage.generate_presigned_url_for_bucket(
            bucket=bucket,
            key=key,
            expires_in=PRESIGNED_URL_EXPIRY_SECONDS,
        )
        return {"url": url, "expires_in": PRESIGNED_URL_EXPIRY_SECONDS}
    except Exception as e:
        logger.warning(
            "audio_presigned_url_failed",
            session_id=session_id,
            bucket=bucket,
            key=key,
            error=str(e),
        )
        raise HTTPException(status_code=404, detail="Audio not found") from None


# -----------------------------------------------------------------------------
# Enhancement Endpoints (M07 Hybrid Mode)
# -----------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/enhancement",
    response_model=EnhancementStatusResponse,
    summary="Get session enhancement status",
    description="Get the status and results of batch enhancement for a realtime session.",
    responses={
        404: {"description": "Session not found"},
    },
)
async def get_session_enhancement(
    session_id: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EnhancementStatusResponse:
    """Get enhancement job status for a realtime session.

    Returns the status of the batch enhancement job triggered when the session
    ended (if enhance_on_end was enabled). When the job is complete, includes
    the enhanced transcript with speaker diarization and word timestamps.
    """
    settings = get_settings()
    session_service = RealtimeSessionService(db, settings)
    jobs_service = JobsService()

    # Get session
    session = await session_service.get_session(session_id)

    if session is None or session.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check if enhancement was requested
    if not session.enhance_on_end:
        return EnhancementStatusResponse(
            session_id=session_id,
            status="not_requested",
        )

    # Check if enhancement job exists
    if session.enhancement_job_id is None:
        # Enhancement was requested but job not created yet
        # This could happen if session is still active or job creation failed
        if session.status == "active":
            return EnhancementStatusResponse(
                session_id=session_id,
                status="pending",
            )
        else:
            return EnhancementStatusResponse(
                session_id=session_id,
                status="failed",
                error="Enhancement job was not created. "
                "Check that store_audio=true was enabled.",
            )

    # Get the enhancement job
    job = await jobs_service.get_job(
        db, session.enhancement_job_id, tenant_id=api_key.tenant_id
    )

    if job is None:
        return EnhancementStatusResponse(
            session_id=session_id,
            status="failed",
            enhancement_job_id=str(session.enhancement_job_id),
            error="Enhancement job not found",
        )

    # Map job status to enhancement status
    job_status = job.status
    if job_status in ("pending", "running"):
        status = "processing"
    elif job_status == "completed":
        status = "completed"
    else:
        status = "failed"

    response = EnhancementStatusResponse(
        session_id=session_id,
        status=status,
        enhancement_job_id=str(job.id),
        job_status=job_status,
    )

    # If completed, fetch the enhanced transcript
    if status == "completed":
        # The merge task output contains the final transcript
        # Find the merge task and get its output
        tasks = await jobs_service.get_job_tasks(
            db, job.id, tenant_id=api_key.tenant_id
        )
        merge_task = next((t for t in tasks if t.stage == "merge"), None)

        if merge_task and merge_task.output_uri:
            try:
                # Fetch transcript from S3
                uri_parts = merge_task.output_uri.replace("s3://", "").split("/", 1)
                bucket = uri_parts[0]
                key = uri_parts[1] if len(uri_parts) > 1 else ""

                async with get_s3_client(settings) as s3:
                    obj = await s3.get_object(Bucket=bucket, Key=key)
                    body = await obj["Body"].read()
                    response.transcript = json.loads(body.decode("utf-8"))
            except Exception as e:
                logger.warning(
                    "enhancement_transcript_fetch_failed",
                    session_id=session_id,
                    job_id=str(job.id),
                    error=str(e),
                )

    # If failed, include error
    if status == "failed" and job.error:
        response.error = job.error

    return response


@router.post(
    "/sessions/{session_id}/enhance",
    response_model=EnhancementStatusResponse,
    summary="Trigger enhancement for a session",
    description="Manually trigger batch enhancement for a completed session that has recorded audio.",
    responses={
        404: {"description": "Session not found"},
        409: {"description": "Enhancement already exists or session not eligible"},
    },
)
async def trigger_session_enhancement(
    session_id: str,
    api_key: RequireJobsRead,
    db: Annotated[AsyncSession, Depends(get_db)],
    enable_diarization: Annotated[
        bool, Query(description="Enable speaker diarization")
    ] = True,
    enable_word_timestamps: Annotated[
        bool, Query(description="Enable word-level timestamps")
    ] = True,
    enable_llm_cleanup: Annotated[
        bool, Query(description="Enable LLM-based cleanup")
    ] = False,
    enable_emotions: Annotated[
        bool, Query(description="Enable emotion detection")
    ] = False,
) -> EnhancementStatusResponse:
    """Manually trigger batch enhancement for a completed session.

    This allows triggering enhancement for sessions that:
    - Had store_audio=true but enhance_on_end=false
    - Need re-enhancement with different options

    Requires the session to have recorded audio (store_audio=true).
    """
    settings = get_settings()
    session_service = RealtimeSessionService(db, settings)
    enhancement_service = EnhancementService(db, settings)

    # Get session
    session = await session_service.get_session(session_id)

    if session is None or session.tenant_id != api_key.tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check if session already has enhancement job
    if session.enhancement_job_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Session already has enhancement job: {session.enhancement_job_id}",
        )

    # Create enhancement job
    try:
        job = await enhancement_service.create_enhancement_job(
            session=session,
            enhance_diarization=enable_diarization,
            enhance_word_timestamps=enable_word_timestamps,
            enhance_llm_cleanup=enable_llm_cleanup,
            enhance_emotions=enable_emotions,
        )
    except EnhancementError as e:
        logger.warning(
            "enhancement_job_creation_failed",
            session_id=session_id,
            error=str(e),
        )
        raise HTTPException(status_code=409, detail=str(e)) from None

    # Publish event for orchestrator to pick up the job
    redis = await _get_redis()
    await publish_job_created(redis, job.id)

    # Update session with enhancement job ID
    await session_service.finalize_session(
        session_id=session_id,
        status=session.status,
        enhancement_job_id=job.id,
    )

    return EnhancementStatusResponse(
        session_id=session_id,
        status="processing",
        enhancement_job_id=str(job.id),
        job_status=job.status,
    )
