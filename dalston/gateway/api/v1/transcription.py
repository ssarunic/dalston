"""Transcription API endpoints.

POST /v1/audio/transcriptions - Submit audio for transcription
GET /v1/audio/transcriptions/{job_id} - Get job status and results
GET /v1/audio/transcriptions - List jobs
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.events import publish_job_created
from dalston.common.models import JobStatus
from dalston.config import Settings
from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.dependencies import get_db, get_redis, get_settings
from dalston.gateway.models.responses import (
    JobCreatedResponse,
    JobListResponse,
    JobResponse,
    JobSummary,
)
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService

router = APIRouter(prefix="/audio/transcriptions", tags=["transcriptions"])

# Service instances
jobs_service = JobsService()


@router.post(
    "",
    response_model=JobCreatedResponse,
    status_code=201,
    summary="Create transcription job",
    description="Upload an audio file for transcription. Returns a job ID to poll for results.",
)
async def create_transcription(
    file: Annotated[UploadFile, File(description="Audio file to transcribe")],
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
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
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
        tenant_id=DEFAULT_TENANT_ID,
        audio_uri="pending",  # Will update after we have the job ID
        parameters=parameters,
        webhook_url=webhook_url,
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
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobResponse:
    """Get job status and transcript if complete.

    1. Fetch job from PostgreSQL
    2. If completed, fetch transcript from S3
    3. Return job with transcript data
    """
    job = await jobs_service.get_job(db, job_id, tenant_id=DEFAULT_TENANT_ID)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build response
    response = JobResponse(
        id=job.id,
        status=JobStatus(job.status),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
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


@router.get(
    "",
    response_model=JobListResponse,
    summary="List transcription jobs",
    description="List transcription jobs with pagination and optional status filter.",
)
async def list_transcriptions(
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    status: Annotated[
        JobStatus | None, Query(description="Filter by status")
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> JobListResponse:
    """List jobs for the current tenant with pagination."""
    jobs, total = await jobs_service.list_jobs(
        db=db,
        tenant_id=DEFAULT_TENANT_ID,
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
