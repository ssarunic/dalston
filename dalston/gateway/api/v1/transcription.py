"""Transcription API endpoints.

POST /v1/audio/transcriptions - Submit audio for transcription
GET /v1/audio/transcriptions/{job_id} - Get job status and results
GET /v1/audio/transcriptions - List jobs
GET /v1/audio/transcriptions/{job_id}/export/{format} - Export transcript
DELETE /v1/audio/transcriptions/{job_id} - Delete a completed/failed job

OpenAI Compatibility (M38):
The POST endpoint also supports OpenAI-style requests. Detection is based on the
`model` parameter - if it matches an OpenAI model ID (whisper-1, gpt-4o-transcribe, etc.),
the request is handled in OpenAI mode with synchronous response and OpenAI response formats.
"""

import asyncio
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

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

from dalston.common.audit import AuditService
from dalston.common.events import publish_job_cancel_requested, publish_job_created
from dalston.common.models import (
    JobStatus,
    validate_retention,
)
from dalston.common.utils import compute_duration_ms
from dalston.config import Settings

# OpenAI compatibility imports (M38)
from dalston.gateway.api.v1.openai_audio import (
    OPENAI_MAX_FILE_SIZE,
    format_openai_response,
    is_openai_model,
    map_openai_model,
    raise_openai_error,
    validate_openai_request,
)
from dalston.gateway.dependencies import (
    RequireJobsRead,
    RequireJobsWrite,
    RequireJobsWriteRateLimited,
    get_audit_service,
    get_db,
    get_export_service,
    get_ingestion_service,
    get_jobs_service,
    get_rate_limiter,
    get_redis,
    get_settings,
)
from dalston.gateway.models.responses import (
    AudioUrlResponse,
    JobCancelledResponse,
    JobCreatedResponse,
    JobListResponse,
    JobResponse,
    JobSummary,
    RetentionInfo,
    StageResponse,
)
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.ingestion import AudioIngestionService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.rate_limiter import RedisRateLimiter
from dalston.gateway.services.storage import StorageService

router = APIRouter(prefix="/audio/transcriptions", tags=["transcriptions"])

# Presigned URL expiry time in seconds (1 hour) - matches realtime endpoint
PRESIGNED_URL_EXPIRY_SECONDS = 3600

logger = structlog.get_logger()


@router.post(
    "",
    response_model=None,
    summary="Create transcription job",
    description="Upload an audio file for transcription. Returns a job ID to poll for results (Dalston native) or the transcript directly (OpenAI compatible).",
    responses={
        200: {"description": "Transcription result (OpenAI mode)"},
        201: {"description": "Job created (Dalston native mode)"},
        400: {"description": "Invalid request"},
        401: {"description": "Invalid API key"},
        408: {"description": "Request timeout (OpenAI mode)"},
    },
)
async def create_transcription(
    request: Request,
    response: Response,
    api_key: RequireJobsWriteRateLimited,
    file: UploadFile | None = File(
        default=None, description="Audio file to transcribe"
    ),
    audio_url: Annotated[
        str | None,
        Form(
            description="URL to audio file (HTTPS, S3/GCS presigned URL, Google Drive, Dropbox)"
        ),
    ] = None,
    model: Annotated[
        str,
        Form(
            description="Engine ID (e.g., faster-whisper-base) or OpenAI model (whisper-1, gpt-4o-transcribe)"
        ),
    ] = "auto",
    language: Annotated[
        str | None, Form(description="Language code or 'auto' for detection")
    ] = None,
    vocabulary: Annotated[
        str | None,
        Form(
            description='JSON array of terms to boost recognition (e.g., \'["Dalston", "FastAPI"]\'). Max 100 terms.',
        ),
    ] = None,
    # OpenAI-compatible parameters (M38)
    prompt: Annotated[
        str | None,
        Form(
            description="OpenAI: Vocabulary hints to guide transcription (max 224 tokens)"
        ),
    ] = None,
    response_format: Annotated[
        str | None,
        Form(
            description="OpenAI: Output format (json, text, srt, verbose_json, vtt, diarized_json)"
        ),
    ] = None,
    temperature: Annotated[
        float | None,
        Form(description="OpenAI: Randomness 0.0-1.0", ge=0.0, le=1.0),
    ] = None,
    timestamp_granularities: Annotated[
        list[str] | None,
        Form(
            alias="timestamp_granularities[]",
            description="OpenAI: Timestamp granularities (word, segment) - requires verbose_json",
        ),
    ] = None,
    # Dalston native parameters
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
    # PII Detection (M26)
    pii_detection: Annotated[
        bool,
        Form(description="Enable PII detection in transcript"),
    ] = False,
    pii_entity_types: Annotated[
        str | None,
        Form(
            description='JSON array of entity types to detect (e.g., \'["ssn","credit_card_number"]\')'
        ),
    ] = None,
    redact_pii_audio: Annotated[
        bool,
        Form(description="Generate redacted audio file with PII removed"),
    ] = False,
    pii_redaction_mode: Annotated[
        str,
        Form(description="Audio redaction mode: 'silence', 'beep'"),
    ] = "silence",
    # Retention: 0=transient, -1=permanent, N=days, None=server default
    retention: Annotated[
        int | None,
        Form(
            description="Retention in days: 0 (transient), -1 (permanent), 1-3650 (days), or omit for server default"
        ),
    ] = None,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    audit_service: AuditService = Depends(get_audit_service),
    ingestion_service: AudioIngestionService = Depends(get_ingestion_service),
    export_service: ExportService = Depends(get_export_service),
) -> JobCreatedResponse | Response | dict[str, Any]:
    """Create a new transcription job.

    Accepts either:
    - file: Direct file upload
    - audio_url: URL to audio file (HTTPS, Google Drive, Dropbox, S3/GCS presigned)

    **OpenAI Compatibility (M38):**
    If `model` is an OpenAI model ID (whisper-1, gpt-4o-transcribe, etc.), the request
    is handled in OpenAI mode: synchronous response with OpenAI response formats.

    **Dalston Native Mode:**
    Returns job ID immediately for async polling.
    """
    # Detect OpenAI mode based on model parameter
    openai_mode = is_openai_model(model)

    # Handle OpenAI-specific validation and parameter mapping
    if openai_mode:
        # Default response_format to json for OpenAI mode
        if response_format is None:
            response_format = "json"

        # Validate OpenAI request parameters
        validate_openai_request(model, response_format, timestamp_granularities)

    # Ingest audio (validates input, downloads from URL if needed, probes metadata)
    try:
        ingested = await ingestion_service.ingest(file=file, url=audio_url)
    except HTTPException as e:
        if openai_mode:
            raise_openai_error(
                e.status_code,
                str(e.detail),
                param="file",
                code="invalid_file_format",
            )
        raise

    # Enforce OpenAI 25MB file size limit
    if openai_mode and len(ingested.content) > OPENAI_MAX_FILE_SIZE:
        raise_openai_error(
            400,
            f"File size exceeds 25MB limit ({len(ingested.content) / 1024 / 1024:.1f}MB)",
            param="file",
            code="file_too_large",
        )

    # Validate per_channel mode requires stereo audio
    if speaker_detection == "per_channel" and ingested.metadata.channels < 2:
        if openai_mode:
            raise_openai_error(
                400,
                f"per_channel speaker detection requires stereo audio, "
                f"but file has {ingested.metadata.channels} channel(s).",
                param="file",
                code="invalid_audio_channels",
            )
        raise HTTPException(
            status_code=400,
            detail=f"per_channel speaker detection requires stereo audio, "
            f"but file has {ingested.metadata.channels} channel(s). "
            f"Use speaker_detection=diarize for mono audio.",
        )

    # Build parameters
    if openai_mode:
        # OpenAI mode: map model to Dalston engine
        engine_id = map_openai_model(model)
        parameters: dict = {
            "language": language or "auto",
            "engine_transcribe": engine_id,
            "timestamps_granularity": "word"
            if timestamp_granularities and "word" in timestamp_granularities
            else "segment",
        }
        # OpenAI uses `prompt` for vocabulary hints
        if prompt:
            parameters["vocabulary"] = prompt
        if temperature is not None and temperature > 0:
            parameters["temperature"] = temperature
    else:
        # Dalston native mode
        # If model is "auto", let orchestrator select engine based on capabilities
        # Otherwise, pass the engine ID directly
        parameters: dict = {
            "language": language or "auto",
            "speaker_detection": speaker_detection,
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "timestamps_granularity": timestamps_granularity,
        }
        if model.lower() != "auto":
            parameters["engine_transcribe"] = model
    # Parse and validate vocabulary (Dalston native mode only)
    if not openai_mode and vocabulary is not None:
        try:
            parsed_vocabulary = json.loads(vocabulary)
            if not isinstance(parsed_vocabulary, list):
                raise HTTPException(
                    status_code=400,
                    detail="vocabulary must be a JSON array of strings",
                )
            if len(parsed_vocabulary) > 100:
                raise HTTPException(
                    status_code=400,
                    detail="vocabulary cannot exceed 100 terms",
                )
            # Validate all items are strings
            for term in parsed_vocabulary:
                if not isinstance(term, str):
                    raise HTTPException(
                        status_code=400,
                        detail="vocabulary must contain only strings",
                    )
            parameters["vocabulary"] = parsed_vocabulary
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON in vocabulary: {e}",
            ) from e

    # Apply server default retention if not specified
    if retention is None:
        retention = settings.retention_default_days

    # Validate retention parameter (0=transient, -1=permanent, 1-3650=days)
    try:
        validate_retention(retention)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # PII detection parameters (M26)
    if pii_detection:
        parameters["pii_detection"] = True
        if pii_entity_types:
            try:
                parsed_entity_types = json.loads(pii_entity_types)
                if isinstance(parsed_entity_types, list):
                    parameters["pii_entity_types"] = parsed_entity_types
            except json.JSONDecodeError:
                # If it's not JSON, treat as comma-separated
                parameters["pii_entity_types"] = [
                    t.strip() for t in pii_entity_types.split(",")
                ]
        if redact_pii_audio:
            parameters["redact_pii_audio"] = True
            parameters["pii_redaction_mode"] = pii_redaction_mode

    # Generate job ID upfront so we can upload to the correct S3 path
    job_id = uuid4()

    # Upload audio to S3
    storage = StorageService(settings)
    audio_uri = await storage.upload_audio(
        job_id=job_id,
        file_content=ingested.content,
        filename=ingested.filename,
    )

    # Parse PII entity types for dedicated column
    pii_entity_types_list: list[str] | None = None
    if pii_detection and pii_entity_types:
        pii_entity_types_list = parameters.get("pii_entity_types")

    # Create job in database
    job = await jobs_service.create_job(
        db=db,
        job_id=job_id,
        tenant_id=api_key.tenant_id,
        audio_uri=audio_uri,
        parameters=parameters,
        audio_format=ingested.metadata.format,
        audio_duration=ingested.metadata.duration,
        audio_sample_rate=ingested.metadata.sample_rate,
        audio_channels=ingested.metadata.channels,
        audio_bit_depth=ingested.metadata.bit_depth,
        # Retention
        retention=retention,
        # PII fields (M26)
        pii_detection_enabled=pii_detection,
        pii_entity_types=pii_entity_types_list,
        pii_redact_audio=redact_pii_audio,
        pii_redaction_mode=pii_redaction_mode if redact_pii_audio else None,
    )

    # Publish event for orchestrator (include request_id for correlation)
    request_id = getattr(request.state, "request_id", None)
    structlog.contextvars.bind_contextvars(job_id=str(job.id))
    await publish_job_created(redis, job.id, request_id=request_id)

    # Track concurrent job for rate limiting
    await rate_limiter.increment_concurrent_jobs(api_key.tenant_id)

    # Audit log job creation
    await audit_service.log_job_created(
        job_id=job.id,
        tenant_id=api_key.tenant_id,
        actor_type="api_key",
        actor_id=api_key.prefix,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # For Dalston native mode, return job ID immediately
    if not openai_mode:
        response.status_code = 201
        return JobCreatedResponse(
            id=job.id,
            status=JobStatus(job.status),
            created_at=job.created_at,
        )

    # OpenAI mode: wait for completion and return result
    storage = StorageService(settings)
    max_wait_seconds = 300
    poll_interval = 1.0
    elapsed = 0.0

    while elapsed < max_wait_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        db.expire(job)
        await db.refresh(job)

        if job.status == JobStatus.COMPLETED.value:
            transcript = await storage.get_transcript(job.id)
            return format_openai_response(
                transcript, response_format, timestamp_granularities, export_service
            )

        if job.status == JobStatus.FAILED.value:
            raise_openai_error(
                500,
                f"Transcription failed: {job.error or 'Unknown error'}",
                error_type="server_error",
                code="processing_failed",
            )

        if job.status == JobStatus.CANCELLED.value:
            raise_openai_error(
                400,
                "Transcription was cancelled",
                code="cancelled",
            )

    # Timeout
    raise_openai_error(
        408,
        "Transcription timeout. The audio file may be too long.",
        error_type="timeout_error",
        code="timeout",
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

    # Build retention info from integer retention field
    # retention: 0=transient, -1=permanent, N=days
    retention_days = job.retention if job.retention is not None else 30

    if retention_days == 0:
        mode = "none"
        hours = None
    elif retention_days == -1:
        mode = "keep"
        hours = None
    else:
        mode = "auto_delete"
        hours = retention_days * 24

    retention_info = RetentionInfo(
        mode=mode,
        hours=hours,
        purge_after=job.purge_after,
        purged_at=job.purged_at,
    )

    # Extract model from parameters (set when user specifies a model)
    model = job.parameters.get("engine_transcribe") if job.parameters else None

    # Build response
    response = JobResponse(
        id=job.id,
        status=JobStatus(job.status),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        model=model,
        error=job.error,
        stages=stages,
        retention=retention_info,
        # Result stats (populated on job completion)
        audio_duration_seconds=job.audio_duration,
        result_language_code=job.result_language_code,
        result_word_count=job.result_word_count,
        result_segment_count=job.result_segment_count,
        result_speaker_count=job.result_speaker_count,
        result_character_count=job.result_character_count,
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
            # PII data (M26)
            response.redacted_text = transcript.get("redacted_text")
            response.entities = transcript.get("pii_entities")
            if transcript.get("pii_metadata"):
                from dalston.gateway.models.responses import PIIInfo

                pii_meta = transcript["pii_metadata"]
                response.pii = PIIInfo(
                    enabled=True,
                    entities_detected=pii_meta.get("entities_detected", 0),
                    entity_summary=pii_meta.get("entity_count_by_type"),
                    redacted_audio_available=pii_meta.get("redacted_audio_uri")
                    is not None,
                )

    return response


@router.get(
    "",
    response_model=JobListResponse,
    summary="List transcription jobs",
    description="List transcription jobs with cursor-based pagination and optional status filter.",
)
async def list_transcriptions(
    api_key: RequireJobsRead,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    cursor: Annotated[
        str | None, Query(description="Pagination cursor from previous response")
    ] = None,
    status: Annotated[JobStatus | None, Query(description="Filter by status")] = None,
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobListResponse:
    """List jobs for the current tenant with cursor-based pagination."""
    jobs, has_more = await jobs_service.list_jobs(
        db=db,
        tenant_id=api_key.tenant_id,
        limit=limit,
        cursor=cursor,
        status=status,
    )

    # Compute next cursor from last job
    next_cursor = (
        jobs_service.encode_job_cursor(jobs[-1]) if jobs and has_more else None
    )

    return JobListResponse(
        jobs=[
            JobSummary(
                id=job.id,
                status=JobStatus(job.status),
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                audio_duration_seconds=job.audio_duration,
                result_language_code=job.result_language_code,
                result_word_count=job.result_word_count,
                result_segment_count=job.result_segment_count,
                result_speaker_count=job.result_speaker_count,
            )
            for job in jobs
        ],
        cursor=next_cursor,
        has_more=has_more,
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


@router.get(
    "/{job_id}/audio",
    response_model=AudioUrlResponse,
    summary="Get original audio download URL",
    description="Get a presigned URL to download the original audio file for a job.",
    responses={
        200: {"description": "Presigned URL for audio download"},
        404: {"description": "Job not found or audio not available"},
        409: {"description": "Job not in terminal state"},
        410: {"description": "Audio has been purged"},
    },
)
async def get_job_audio(
    job_id: UUID,
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> AudioUrlResponse:
    """Get presigned URL for original job audio.

    Requirements:
    1. Job must exist and belong to the tenant
    2. Job must be in a terminal state (completed, failed, cancelled)
    3. Audio must not have been purged by retention policy
    """
    job = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check job is in terminal state
    terminal_states = {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }
    if job.status not in terminal_states:
        raise HTTPException(
            status_code=409,
            detail=f"Job not in terminal state. Current status: {job.status}",
        )

    # Check if audio has been purged by retention policy
    if job.purged_at is not None:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "audio_purged",
                "message": "Audio has been purged according to retention policy",
                "purged_at": job.purged_at.isoformat(),
            },
        )

    # Parse and validate S3 URI
    storage = StorageService(settings)
    try:
        bucket, key = storage.parse_s3_uri(job.audio_uri or "")
    except ValueError:
        raise HTTPException(
            status_code=404, detail="Original audio not found"
        ) from None

    if bucket != settings.s3_bucket:
        logger.warning(
            "audio_bucket_mismatch",
            job_id=str(job_id),
            expected_bucket=settings.s3_bucket,
            actual_bucket=bucket,
        )
        raise HTTPException(status_code=404, detail="Original audio not found")

    # Verify exact S3 object exists (handles manual deletion via DELETE /audio)
    if not await storage.object_exists(key):
        raise HTTPException(
            status_code=410,
            detail={
                "code": "audio_deleted",
                "message": "Audio has been deleted",
            },
        )

    # Generate presigned URL
    try:
        url = await storage.generate_presigned_url(
            key,
            expires_in=PRESIGNED_URL_EXPIRY_SECONDS,
        )
        return AudioUrlResponse(
            url=url,
            expires_in=PRESIGNED_URL_EXPIRY_SECONDS,
            type="original",
        )
    except Exception as e:
        logger.warning(
            "audio_presigned_url_failed",
            job_id=str(job_id),
            key=key,
            error=str(e),
        )
        raise HTTPException(
            status_code=404,
            detail="Failed to generate audio download URL",
        ) from None


@router.get(
    "/{job_id}/audio/redacted",
    response_model=AudioUrlResponse,
    summary="Get redacted audio download URL",
    description="Get a presigned URL to download the PII-redacted audio file for a job.",
    responses={
        200: {"description": "Presigned URL for redacted audio download"},
        404: {"description": "Job not found or redacted audio not available"},
        409: {"description": "Job not completed"},
        410: {"description": "Audio has been purged"},
    },
)
async def get_job_audio_redacted(
    job_id: UUID,
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> AudioUrlResponse:
    """Get presigned URL for PII-redacted job audio.

    Requirements:
    1. Job must exist and belong to the tenant
    2. Job must be completed (redaction only happens on successful jobs)
    3. PII detection with audio redaction must have been enabled
    4. Audio must not have been purged by retention policy
    5. Redacted audio must actually exist in S3

    Note: Uses transcript's pii_metadata.redacted_audio_uri as source of truth,
    matching the UI's redacted_audio_available flag behavior.
    """
    job = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Redacted audio only exists for completed jobs
    if job.status != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Job not completed. Current status: {job.status}. "
            "Redacted audio is only available for completed jobs.",
        )

    # Check if PII redaction was enabled
    if not job.pii_redact_audio:
        raise HTTPException(
            status_code=404,
            detail="PII audio redaction was not enabled for this job",
        )

    # Check if audio has been purged
    if job.purged_at is not None:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "audio_purged",
                "message": "Audio has been purged according to retention policy",
                "purged_at": job.purged_at.isoformat(),
            },
        )

    # Get redacted audio URI from transcript's pii_metadata (matches UI availability flag)
    # This ensures consistency with the UI's redacted_audio_available indicator
    storage = StorageService(settings)
    transcript = await storage.get_transcript(job.id)

    if not transcript:
        raise HTTPException(
            status_code=404,
            detail="Transcript not found",
        )

    pii_metadata = transcript.get("pii_metadata")
    if not pii_metadata:
        raise HTTPException(
            status_code=404,
            detail="PII metadata not available in transcript",
        )

    redacted_audio_uri = pii_metadata.get("redacted_audio_uri")
    if not redacted_audio_uri:
        raise HTTPException(
            status_code=404,
            detail="Redacted audio not available. PII redaction may not have completed successfully.",
        )

    # Parse and validate S3 URI
    try:
        bucket, key = storage.parse_s3_uri(redacted_audio_uri)
    except ValueError:
        logger.warning(
            "invalid_redacted_audio_uri",
            job_id=str(job_id),
            uri=redacted_audio_uri,
        )
        raise HTTPException(
            status_code=404, detail="Redacted audio not found"
        ) from None

    if bucket != settings.s3_bucket:
        logger.warning(
            "redacted_audio_bucket_mismatch",
            job_id=str(job_id),
            expected_bucket=settings.s3_bucket,
            actual_bucket=bucket,
        )
        raise HTTPException(status_code=404, detail="Redacted audio not found")

    # Verify the S3 object exists (handles manual deletion)
    if not await storage.object_exists(key):
        raise HTTPException(
            status_code=410,
            detail={
                "code": "audio_deleted",
                "message": "Redacted audio has been deleted",
            },
        )

    # Generate presigned URL
    try:
        url = await storage.generate_presigned_url(
            key,
            expires_in=PRESIGNED_URL_EXPIRY_SECONDS,
        )
        return AudioUrlResponse(
            url=url,
            expires_in=PRESIGNED_URL_EXPIRY_SECONDS,
            type="redacted",
        )
    except Exception as e:
        logger.warning(
            "redacted_audio_presigned_url_failed",
            job_id=str(job_id),
            key=key,
            error=str(e),
        )
        raise HTTPException(
            status_code=404,
            detail="Failed to generate redacted audio download URL",
        ) from None


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
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
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
    5. If immediate CANCELLED, decrement concurrent job counter
    """
    try:
        result = await jobs_service.cancel_job(db, job_id, tenant_id=api_key.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Publish event for orchestrator to remove tasks from Redis queues
    await publish_job_cancel_requested(redis, job_id)

    # If job was immediately cancelled (no running tasks), decrement counter now.
    # Uses idempotent helper to prevent double-decrement if orchestrator also tries.
    if result.status == JobStatus.CANCELLED:
        await rate_limiter.decrement_concurrent_jobs_once(job_id, api_key.tenant_id)

    return JobCancelledResponse(
        id=result.job.id,
        status=result.status,
        message=result.message,
    )


@router.delete(
    "/{job_id}/audio",
    status_code=204,
    summary="Delete job audio",
    description="Delete audio and intermediate artifacts for a job, preserving the transcript. Job must be in terminal state.",
    responses={
        204: {"description": "Audio deleted successfully"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a terminal state"},
        410: {"description": "Audio already purged"},
    },
)
async def delete_audio(
    request: Request,
    job_id: UUID,
    api_key: RequireJobsWrite,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    """Delete audio while preserving transcript.

    1. Validate job exists and is in a terminal state
    2. Check audio hasn't already been purged
    3. Delete S3 audio and task artifacts (keep transcript)
    4. Return 204 No Content
    """
    job = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check job is in terminal state
    terminal_states = {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }
    if job.status not in terminal_states:
        raise HTTPException(
            status_code=409,
            detail=f"Job not in terminal state. Current status: {job.status}",
        )

    # Check if audio already purged
    storage = StorageService(settings)
    if not await storage.has_audio(job_id):
        raise HTTPException(status_code=410, detail="Audio already purged")

    # Delete audio and task artifacts (preserves transcript)
    await storage.delete_job_audio(job_id)

    # Audit log
    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_audio_deleted(
        job_id=job_id,
        tenant_id=api_key.tenant_id,
        actor_type="api_key",
        actor_id=api_key.prefix,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
    )

    return Response(status_code=204)


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
    request: Request,
    job_id: UUID,
    api_key: RequireJobsWrite,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response:
    """Delete a job and all associated artifacts.

    1. Validate job exists and is in a terminal state
    2. Delete S3 artifacts (audio, task outputs, transcript)
    3. Delete database record (cascades to tasks)
    4. Return 204 No Content
    """
    # Save tenant_id before deletion
    job_for_tenant = await jobs_service.get_job(db, job_id, tenant_id=api_key.tenant_id)
    if job_for_tenant is None:
        raise HTTPException(status_code=404, detail="Job not found")
    tenant_id = job_for_tenant.tenant_id

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

    # Audit log
    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_job_deleted(
        job_id=job_id,
        tenant_id=tenant_id,
        actor_type="api_key",
        actor_id=api_key.prefix,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
    )

    return Response(status_code=204)
