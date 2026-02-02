"""ElevenLabs-compatible Speech-to-Text API endpoints.

GET /v1/speech-to-text/transcripts/{transcription_id}/export/{format}

Note: ElevenLabs uses xi-api-key header for authentication.
This is supported by the auth middleware alongside Bearer tokens.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.models import JobStatus
from dalston.config import Settings
from dalston.gateway.dependencies import (
    RequireJobsRead,
    get_db,
    get_export_service,
    get_jobs_service,
    get_settings,
)
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService

router = APIRouter(prefix="/speech-to-text", tags=["speech-to-text"])


@router.get(
    "/transcripts/{transcription_id}/export/{format}",
    summary="Export transcript (ElevenLabs compatible)",
    description="Export transcript in specified format. ElevenLabs-compatible endpoint.",
    responses={
        200: {
            "description": "Exported transcript",
            "content": {
                "text/plain": {"schema": {"type": "string"}},
                "text/vtt": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "object"}},
            },
        },
        400: {"description": "Transcription not completed or unsupported format"},
        404: {"description": "Transcription not found"},
    },
)
async def export_transcript(
    transcription_id: UUID,
    format: str,
    api_key: RequireJobsRead,
    include_speakers: Annotated[
        bool, Query(description="Include speaker labels in output")
    ] = True,
    max_line_length: Annotated[
        int, Query(ge=10, le=200, description="Max characters per subtitle line")
    ] = 42,
    max_lines: Annotated[
        int, Query(ge=1, le=10, description="Max lines per subtitle block")
    ] = 2,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    export_service: ExportService = Depends(get_export_service),
) -> Response:
    """Export transcript in specified format (ElevenLabs compatible).

    Supported formats:
    - srt: SubRip subtitle format
    - webvtt: WebVTT subtitle format
    - txt: Plain text
    - json: Full transcript JSON

    Note: ElevenLabs uses 'webvtt' while Dalston native uses 'vtt'.
    Both are supported.
    """
    # Validate format
    export_format = export_service.validate_format(format)

    # Get job (transcription_id maps to job_id internally)
    job = await jobs_service.get_job(db, transcription_id, tenant_id=api_key.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Transcription not found")

    # Check job is completed
    if job.status != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Transcription not completed. Current status: {job.status}",
        )

    # Fetch transcript from S3
    storage = StorageService(settings)
    transcript = await storage.get_transcript(job.id)

    # Generate and return export response
    return export_service.create_export_response(
        transcript=transcript,
        export_format=export_format,
        include_speakers=include_speakers,
        max_line_length=max_line_length,
        max_lines=max_lines,
    )
