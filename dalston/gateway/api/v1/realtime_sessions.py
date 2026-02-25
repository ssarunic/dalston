"""Real-time session history endpoints.

GET /v1/realtime/sessions - List sessions
GET /v1/realtime/sessions/{session_id} - Get session details
DELETE /v1/realtime/sessions/{session_id} - Delete session
GET /v1/realtime/sessions/{session_id}/transcript - Get transcript
GET /v1/realtime/sessions/{session_id}/audio - Get audio URL
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

from dalston.common.s3 import get_s3_client
from dalston.config import get_settings
from dalston.db.models import RealtimeSessionModel
from dalston.db.session import get_db
from dalston.gateway.dependencies import RequireJobsRead
from dalston.gateway.services.export import ExportService
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
    retention: int  # 0=transient, -1=permanent, N=days
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
    retention: int  # 0=transient, -1=permanent, N=days
    purge_after: str | None
    purged_at: str | None
    audio_uri: str | None
    transcript_uri: str | None
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

    def _build_session_summary(s: RealtimeSessionModel) -> SessionSummary:
        return SessionSummary(
            id=str(s.id),
            status=s.status,
            language=s.language,
            model=s.model,
            engine=s.engine,
            audio_duration_seconds=s.audio_duration_seconds,
            segment_count=s.segment_count,
            word_count=s.word_count,
            retention=s.retention if s.retention is not None else 30,
            started_at=s.started_at.isoformat(),
            ended_at=s.ended_at.isoformat() if s.ended_at else None,
        )

    return SessionsListResponse(
        sessions=[_build_session_summary(s) for s in sessions],
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
        retention=session.retention if session.retention is not None else 30,
        purge_after=session.purge_after.isoformat() if session.purge_after else None,
        purged_at=session.purged_at.isoformat() if session.purged_at else None,
        audio_uri=session.audio_uri,
        transcript_uri=session.transcript_uri,
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
    storage = StorageService(settings)
    try:
        bucket, key = storage.parse_s3_uri(session.transcript_uri)
    except ValueError:
        raise HTTPException(status_code=404, detail="Transcript not found") from None

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
    storage = StorageService(settings)
    try:
        bucket, key = storage.parse_s3_uri(session.transcript_uri)
    except ValueError:
        raise HTTPException(status_code=404, detail="Transcript not found") from None

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

    # Generate presigned URL from S3 URI
    storage = StorageService(settings)
    try:
        url = await storage.generate_presigned_url_from_uri(
            session.audio_uri,
            expires_in=PRESIGNED_URL_EXPIRY_SECONDS,
            require_expected_bucket=False,  # Realtime sessions may use different buckets
        )
        return {"url": url, "expires_in": PRESIGNED_URL_EXPIRY_SECONDS}
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid audio URI") from None
    except Exception as e:
        logger.warning(
            "audio_presigned_url_failed",
            session_id=session_id,
            uri=session.audio_uri,
            error=str(e),
        )
        raise HTTPException(status_code=404, detail="Audio not found") from None
