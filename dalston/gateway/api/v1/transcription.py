"""Transcription API endpoints.

POST /v1/audio/transcriptions - Submit audio for transcription
GET /v1/audio/transcriptions/{job_id} - Get job status and results
GET /v1/audio/transcriptions - List jobs
GET /v1/audio/transcriptions/{job_id}/export/{format} - Export transcript
"""

import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.events import publish_job_created
from dalston.common.models import JobStatus
from dalston.config import WEBHOOK_METADATA_MAX_SIZE, Settings
from dalston.gateway.dependencies import (
    RequireJobsRead,
    RequireJobsWrite,
    get_db,
    get_export_service,
    get_jobs_service,
    get_redis,
    get_settings,
)
from dalston.gateway.models.responses import (
    JobCreatedResponse,
    JobListResponse,
    JobResponse,
    JobSummary,
    StageResponse,
)
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService

router = APIRouter(prefix="/audio/transcriptions", tags=["transcriptions"])


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=201,
    summary="Create transcription job",
    description="Upload an audio file for transcription. Returns a job ID to poll for results.",
)
async def create_transcription(
    file: Annotated[UploadFile, File(description="Audio file to transcribe")],
    api_key: RequireJobsWrite,
    language: Annotated[
        str, Form(description="Language code or 'auto' for detection")
    ] = "auto",
    speaker_detection: Annotated[
        str, Form(description="Speaker detection: 'none', 'diarize', 'per_channel'")
    ] = "none",
    num_speakers: Annotated[
        int | None, Form(description="Expected number of speakers", ge=1, le=32)
    ] = None,
    timestamps_granularity: Annotated[
        str, Form(description="Timestamp granularity: 'none', 'segment', 'word'")
    ] = "word",
    webhook_url: Annotated[
        str | None, Form(description="Webhook URL for completion callback")
    ] = None,
    webhook_metadata: Annotated[
        str | None,
        Form(description="JSON object echoed in webhook callback (max 16KB)"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobCreatedResponse:
    """Create a new transcription job.

    1. Upload audio file to S3
    2. Create job record in PostgreSQL
    3. Publish job.created event to Redis
    4. Return job ID for polling
    """
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="File must have a filename")

    # Parse webhook_metadata JSON string
    parsed_webhook_metadata: dict | None = None
    if webhook_metadata:
        try:
            parsed_webhook_metadata = json.loads(webhook_metadata)
            if not isinstance(parsed_webhook_metadata, dict):
                raise HTTPException(
                    status_code=400,
                    detail="webhook_metadata must be a JSON object",
                )
            # Validate size
            if len(webhook_metadata) > WEBHOOK_METADATA_MAX_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"webhook_metadata exceeds maximum size of {WEBHOOK_METADATA_MAX_SIZE // 1024}KB",
                )
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON in webhook_metadata: {e}",
            ) from e

    # Build parameters
    parameters = {
        "language": language,
        "speaker_detection": speaker_detection,
        "num_speakers": num_speakers,
        "timestamps_granularity": timestamps_granularity,
    }

    # Upload audio to S3
    storage = StorageService(settings)
    audio_uri = await storage.upload_audio(
        job_id=UUID("00000000-0000-0000-0000-000000000000"),  # Temporary, will update
        file=file,
    )

    # Create job in database
    # Note: We create the job first to get the ID, then re-upload with correct path
    job = await jobs_service.create_job(
        db=db,
        tenant_id=api_key.tenant_id,
        audio_uri="pending",  # Will update after we have the job ID
        parameters=parameters,
        webhook_url=webhook_url,
        webhook_metadata=parsed_webhook_metadata,
    )

    # Re-upload with correct job ID path
    await file.seek(0)  # Reset file position
    audio_uri = await storage.upload_audio(job_id=job.id, file=file)

    # Update job with correct audio URI
    job.audio_uri = audio_uri
    await db.commit()
    await db.refresh(job)

    # Publish event for orchestrator
    await publish_job_created(redis, job.id)

    return JobCreatedResponse(
        id=job.id,
        status=JobStatus(job.status),
        created_at=job.created_at,
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get transcription job",
    description="Get the status and results of a transcription job.",
)
async def get_transcription(
    job_id: UUID,
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobResponse:
    """Get job status and transcript if complete.

    1. Fetch job from PostgreSQL with tasks
    2. Build stages array from tasks
    3. If completed, fetch transcript from S3
    4. Return job with transcript data and stages
    """
    job = await jobs_service.get_job_with_tasks(db, job_id, tenant_id=api_key.tenant_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build stages array from tasks (if any)
    stages = None
    if job.tasks:
        sorted_tasks = jobs_service._topological_sort_tasks(list(job.tasks))
        stages = [
            StageResponse(
                stage=task.stage,
                task_id=task.id,
                engine_id=task.engine_id,
                status=task.status,
                required=task.required,
                started_at=task.started_at,
                completed_at=task.completed_at,
                duration_ms=_compute_duration_ms(task.started_at, task.completed_at),
                retries=task.retries if task.retries > 0 else None,
                error=task.error,
            )
            for task in sorted_tasks
        ]

    # Build response
    response = JobResponse(
        id=job.id,
        status=JobStatus(job.status),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
        stages=stages,
    )

    # If completed, fetch transcript from S3
    if job.status == JobStatus.COMPLETED.value:
        storage = StorageService(settings)
        transcript = await storage.get_transcript(job.id)

        if transcript:
            response.language_code = transcript.get("metadata", {}).get("language")
            response.text = transcript.get("text")
            response.words = transcript.get("words")
            response.segments = transcript.get("segments")
            response.speakers = transcript.get("speakers")

    return response


def _compute_duration_ms(
    started_at: "datetime | None", completed_at: "datetime | None"
) -> int | None:
    """Compute duration in milliseconds from timestamps."""
    if started_at is None or completed_at is None:
        return None
    delta = completed_at - started_at
    return int(delta.total_seconds() * 1000)


@router.get(
    "",
    response_model=JobListResponse,
    summary="List transcription jobs",
    description="List transcription jobs with pagination and optional status filter.",
)
async def list_transcriptions(
    api_key: RequireJobsRead,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    status: Annotated[JobStatus | None, Query(description="Filter by status")] = None,
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobListResponse:
    """List jobs for the current tenant with pagination."""
    jobs, total = await jobs_service.list_jobs(
        db=db,
        tenant_id=api_key.tenant_id,
        limit=limit,
        offset=offset,
        status=status,
    )

    return JobListResponse(
        jobs=[
            JobSummary(
                id=job.id,
                status=JobStatus(job.status),
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
            )
            for job in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{job_id}/export/{format}",
    summary="Export transcription",
    description="Export transcript in specified format: srt, vtt, txt, json",
    responses={
        200: {
            "description": "Exported transcript",
            "content": {
                "text/plain": {"schema": {"type": "string"}},
                "text/vtt": {"schema": {"type": "string"}},
                "application/json": {"schema": {"type": "object"}},
            },
        },
        400: {"description": "Job not completed or unsupported format"},
        404: {"description": "Job not found"},
    },
)
async def export_transcription(
    job_id: UUID,
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
    """Export transcript in specified format.

    Supported formats:
    - srt: SubRip subtitle format (uses segments)
    - vtt/webvtt: WebVTT subtitle format (uses segments)
    - txt: Plain text with speaker labels (uses words when available)
    - json: Full transcript JSON
    """
    # Validate format
    export_format = export_service.validate_format(format)

    # Get job
    job = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check job is completed
    if job.status != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Current status: {job.status}",
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
