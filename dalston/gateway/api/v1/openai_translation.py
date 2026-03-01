"""OpenAI-compatible Audio Translation API endpoint.

POST /v1/audio/translations - Translate audio to English

This is a standalone endpoint since Dalston native doesn't have a translation endpoint.
"""

import asyncio
from typing import Annotated, Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.audit import AuditService
from dalston.common.events import publish_job_created
from dalston.common.models import JobStatus
from dalston.config import Settings
from dalston.gateway.api.v1.openai_audio import (
    OPENAI_MAX_FILE_SIZE,
    format_openai_response,
    map_openai_model,
    raise_openai_error,
)
from dalston.gateway.dependencies import (
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
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.ingestion import AudioIngestionService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.rate_limiter import RedisRateLimiter
from dalston.gateway.services.storage import StorageService

router = APIRouter(prefix="/audio", tags=["openai"])


@router.post(
    "/translations",
    response_model=None,
    summary="Translate audio to English (OpenAI compatible)",
    description="Translate audio to English using OpenAI-compatible parameters.",
    responses={
        200: {"description": "Translation result"},
        400: {"description": "Invalid request"},
        401: {"description": "Invalid API key"},
        408: {"description": "Request timeout"},
        422: {"description": "Invalid file format"},
    },
)
async def create_translation_openai(
    request: Request,
    api_key: RequireJobsWriteRateLimited,
    file: UploadFile = File(..., description="Audio file to translate"),
    model: Annotated[
        str,
        Form(description="Model ID: whisper-1"),
    ] = "whisper-1",
    prompt: Annotated[
        str | None,
        Form(description="Vocabulary hints to guide translation (max 224 tokens)"),
    ] = None,
    response_format: Annotated[
        str,
        Form(description="Output format: json, text, srt, verbose_json, vtt"),
    ] = "json",
    temperature: Annotated[
        float,
        Form(description="Randomness 0.0-1.0", ge=0.0, le=1.0),
    ] = 0.0,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    ingestion_service: AudioIngestionService = Depends(get_ingestion_service),
    export_service: ExportService = Depends(get_export_service),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    audit_service: AuditService = Depends(get_audit_service),
) -> Response | dict[str, Any]:
    """Translate audio to English using OpenAI-compatible API.

    This endpoint accepts audio in any supported language and translates it to English.
    The translation is performed by the transcription engine with English output forced.
    """
    # Validate model (only whisper-1 supports translation)
    if model != "whisper-1":
        raise_openai_error(
            400,
            "Translation only supports whisper-1 model",
            param="model",
            code="invalid_model",
        )

    # Validate response format
    valid_formats = {"json", "text", "srt", "verbose_json", "vtt"}
    if response_format not in valid_formats:
        raise_openai_error(
            400,
            f"Invalid response_format: {response_format}. Supported: {', '.join(valid_formats)}.",
            param="response_format",
            code="invalid_response_format",
        )

    # Ingest audio
    try:
        ingested = await ingestion_service.ingest(file=file, url=None)
    except HTTPException as e:
        raise_openai_error(
            e.status_code,
            str(e.detail),
            param="file",
            code="invalid_file_format",
        )

    # Enforce OpenAI 25MB file size limit
    if len(ingested.content) > OPENAI_MAX_FILE_SIZE:
        raise_openai_error(
            400,
            f"File size exceeds 25MB limit ({len(ingested.content) / 1024 / 1024:.1f}MB)",
            param="file",
            code="file_too_large",
        )

    # Build Dalston parameters - force English output for translation
    parameters: dict[str, Any] = {
        "language": "en",  # Translation always outputs English
        "engine_transcribe": map_openai_model(model),
        "timestamps_granularity": "segment",
        "task": "translate",  # Enable translation mode
    }

    if prompt:
        parameters["vocabulary"] = prompt

    if temperature > 0:
        parameters["temperature"] = temperature

    # Generate job ID
    job_id = uuid4()

    # Upload audio to S3
    storage = StorageService(settings)
    audio_uri = await storage.upload_audio(
        job_id=job_id,
        file_content=ingested.content,
        filename=ingested.filename,
    )

    # Create job
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
    )

    # Publish job.created event
    await publish_job_created(redis, job.id)

    # Track concurrent job
    await rate_limiter.increment_concurrent_jobs(api_key.tenant_id)

    # Audit log
    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_job_created(
        job_id=job.id,
        tenant_id=api_key.tenant_id,
        actor_type="api_key",
        actor_id=api_key.prefix,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Wait for completion (synchronous)
    max_wait_seconds = 300
    poll_interval = 1.0
    elapsed = 0.0

    while elapsed < max_wait_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        db.expire(job)
        await db.refresh(job)

        if job.status == JobStatus.COMPLETED.value:
            transcript = await storage.get_transcript(job_id)
            return format_openai_response(
                transcript, response_format, None, export_service
            )

        if job.status == JobStatus.FAILED.value:
            raise_openai_error(
                500,
                f"Translation failed: {job.error or 'Unknown error'}",
                error_type="server_error",
                code="processing_failed",
            )

        if job.status == JobStatus.CANCELLED.value:
            raise_openai_error(
                400,
                "Translation was cancelled",
                code="cancelled",
            )

    # Timeout
    raise_openai_error(
        408,
        "Translation timeout. The audio file may be too long.",
        error_type="timeout_error",
        code="timeout",
    )
