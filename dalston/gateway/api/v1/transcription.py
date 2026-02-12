"""Transcription API endpoints.

POST /v1/audio/transcriptions - Submit audio for transcription
GET /v1/audio/transcriptions/{job_id} - Get job status and results
GET /v1/audio/transcriptions - List jobs
GET /v1/audio/transcriptions/{job_id}/export/{format} - Export transcript
DELETE /v1/audio/transcriptions/{job_id} - Delete a completed/failed job
"""

import asyncio
import json
import logging
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.events import publish_job_cancel_requested, publish_job_created
from dalston.common.models import DEFAULT_MODEL, JobStatus, resolve_model
from dalston.common.utils import compute_duration_ms
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
    JobCancelledResponse,
    JobCreatedResponse,
    JobListResponse,
    JobResponse,
    JobSummary,
    StageResponse,
)
from dalston.gateway.services.audio_probe import (
    InvalidAudioError,
    probe_audio,
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
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File(description="Audio file to transcribe")],
    api_key: RequireJobsWrite,
    model: Annotated[
        str,
        Form(
            description="Transcription model (e.g., whisper-large-v3, whisper-base, fast)"
        ),
    ] = DEFAULT_MODEL,
    language: Annotated[
        str, Form(description="Language code or 'auto' for detection")
    ] = "auto",
    initial_prompt: Annotated[
        str | None,
        Form(
            description="Domain vocabulary hints to improve accuracy (e.g., technical terms, names)",
            max_length=1000,
        ),
    ] = None,
    speaker_detection: Annotated[
        str, Form(description="Speaker detection: 'none', 'diarize', 'per_channel'")
    ] = "none",
    num_speakers: Annotated[
        int | None, Form(description="Exact number of speakers", ge=1, le=32)
    ] = None,
    min_speakers: Annotated[
        int | None,
        Form(
            description="Minimum speakers for diarization auto-detection", ge=1, le=32
        ),
    ] = None,
    max_speakers: Annotated[
        int | None,
        Form(
            description="Maximum speakers for diarization auto-detection", ge=1, le=32
        ),
    ] = None,
    timestamps_granularity: Annotated[
        str, Form(description="Timestamp granularity: 'none', 'segment', 'word'")
    ] = "word",
    webhook_url: Annotated[
        str | None,
        Form(
            description="Webhook URL for completion callback (deprecated, use registered webhooks)"
        ),
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

    # Check per-job webhook_url deprecation
    if webhook_url:
        if not settings.allow_per_job_webhooks:
            raise HTTPException(
                status_code=400,
                detail="Per-job webhook_url is disabled. Use registered webhooks via POST /v1/webhooks instead.",
            )
        # Add deprecation warning header
        response.headers["Deprecation"] = "true"
        response.headers["X-Deprecation-Notice"] = (
            "webhook_url parameter is deprecated. Use registered webhooks via POST /v1/webhooks."
        )

    # Validate and resolve model
    try:
        model_def = resolve_model(model)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "type": "invalid_request_error",
                "message": str(e),
                "param": "model",
            },
        ) from e

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

    # Read file content for probing
    file_content = await file.read()

    # Probe audio to extract metadata and validate
    # Uses to_thread() because probe_audio calls ffprobe synchronously
    try:
        audio_metadata = await asyncio.to_thread(
            probe_audio, file_content, file.filename
        )
    except InvalidAudioError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Validate per_channel mode requires stereo audio
    if speaker_detection == "per_channel" and audio_metadata.channels < 2:
        raise HTTPException(
            status_code=400,
            detail=f"per_channel speaker detection requires stereo audio, "
            f"but file has {audio_metadata.channels} channel(s). "
            f"Use speaker_detection=diarize for mono audio.",
        )

    # Build parameters with resolved model info
    parameters = {
        "model": model_def.id,
        "engine_transcribe": model_def.engine,
        "transcribe_config": {
            "model": model_def.engine_model,
        },
        "language": language,
        "speaker_detection": speaker_detection,
        "num_speakers": num_speakers,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
        "timestamps_granularity": timestamps_granularity,
    }
    # Only include optional parameters if set
    if initial_prompt is not None:
        parameters["initial_prompt"] = initial_prompt

    # Upload audio to S3
    storage = StorageService(settings)
    audio_uri = await storage.upload_audio(
        job_id=UUID("00000000-0000-0000-0000-000000000000"),  # Temporary, will update
        file=file,
        file_content=file_content,
    )

    # Create job in database
    job = await jobs_service.create_job(
        db=db,
        tenant_id=api_key.tenant_id,
        audio_uri="pending",  # Will update after we have the job ID
        parameters=parameters,
        webhook_url=webhook_url,
        webhook_metadata=parsed_webhook_metadata,
        audio_format=audio_metadata.format,
        audio_duration=audio_metadata.duration,
        audio_sample_rate=audio_metadata.sample_rate,
        audio_channels=audio_metadata.channels,
        audio_bit_depth=audio_metadata.bit_depth,
    )

    # Re-upload with correct job ID path
    audio_uri = await storage.upload_audio(
        job_id=job.id, file=file, file_content=file_content
    )

    # Update job with correct audio URI
    job.audio_uri = audio_uri
    await db.commit()
    await db.refresh(job)

    # Publish event for orchestrator (include request_id for correlation)
    request_id = getattr(request.state, "request_id", None)
    structlog.contextvars.bind_contextvars(job_id=str(job.id))
    await publish_job_created(redis, job.id, request_id=request_id)

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
                duration_ms=compute_duration_ms(task.started_at, task.completed_at),
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


logger = logging.getLogger(__name__)


@router.post(
    "/{job_id}/cancel",
    response_model=JobCancelledResponse,
    summary="Cancel transcription job",
    description="Cancel a pending or running transcription job. Running tasks complete naturally.",
    responses={
        200: {"description": "Cancellation requested"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a cancellable state"},
    },
)
async def cancel_transcription(
    job_id: UUID,
    api_key: RequireJobsWrite,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobCancelledResponse:
    """Cancel a transcription job.

    Cancellation is "soft": running tasks complete naturally, only
    queued/pending work is cancelled. This follows industry practice
    (AWS, Google, AssemblyAI) since ML inference has no safe interruption points.

    Steps:
    1. Validate job exists and is cancellable (PENDING or RUNNING)
    2. Mark PENDING/READY tasks as CANCELLED
    3. Set job status to CANCELLING (or CANCELLED if nothing running)
    4. Publish job.cancel_requested event for orchestrator
    """
    try:
        result = await jobs_service.cancel_job(db, job_id, tenant_id=api_key.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Publish event for orchestrator to remove tasks from Redis queues
    await publish_job_cancel_requested(redis, job_id)

    return JobCancelledResponse(
        id=result.job.id,
        status=result.status,
        message=result.message,
    )


@router.delete(
    "/{job_id}",
    status_code=204,
    summary="Delete transcription job",
    description="Delete a transcription job and its artifacts. Only terminal-state jobs (completed, failed, cancelled) can be deleted.",
    responses={
        204: {"description": "Job deleted successfully"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a terminal state"},
    },
)
async def delete_transcription(
    job_id: UUID,
    api_key: RequireJobsWrite,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> Response:
    """Delete a job and all associated artifacts.

    1. Validate job exists and is in a terminal state
    2. Delete S3 artifacts (audio, task outputs, transcript)
    3. Delete database record (cascades to tasks)
    4. Return 204 No Content
    """
    try:
        job = await jobs_service.delete_job(db, job_id, tenant_id=api_key.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Clean up S3 artifacts (best-effort; DB record is already gone)
    try:
        storage = StorageService(settings)
        await storage.delete_job_artifacts(job_id)
    except Exception:
        logger.warning(
            "Failed to delete S3 artifacts for job %s", job_id, exc_info=True
        )

    return Response(status_code=204)
