"""OpenAI-compatible Audio Translation API endpoint.

POST /v1/audio/translations - Translate audio to English

This is a standalone endpoint since Dalston native doesn't have a translation endpoint.
"""

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
from dalston.common.model_selection_keys import MODEL_PARAM_TRANSCRIBE
from dalston.gateway.api.v1.openai_audio import (
    OPENAI_MAX_FILE_SIZE,
    OpenAIEndpoint,
    format_openai_response,
    map_openai_model,
    map_openai_runtime_model,
    raise_openai_error,
    validate_openai_request,
)
from dalston.gateway.dependencies import (
    get_audit_service,
    get_db,
    get_export_service,
    get_ingestion_service,
    get_jobs_service,
    get_principal_with_job_rate_limit,
    get_rate_limiter,
    get_redis,
    get_security_manager,
    get_storage_service,
)
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.ingestion import AudioIngestionService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.polling import wait_for_job_completion
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
    response: Response,
    principal: Annotated[Principal, Depends(get_principal_with_job_rate_limit)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
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
    jobs_service: JobsService = Depends(get_jobs_service),
    ingestion_service: AudioIngestionService = Depends(get_ingestion_service),
    export_service: ExportService = Depends(get_export_service),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    audit_service: AuditService = Depends(get_audit_service),
    storage: StorageService = Depends(get_storage_service),
) -> Response | dict[str, Any]:
    """Translate audio to English using OpenAI-compatible API.

    This endpoint accepts audio in any supported language and translates it to English.
    The translation is performed by the transcription engine with English output forced.
    """
    security_manager.require_permission(principal, Permission.JOB_CREATE)
    openai_rate_headers = getattr(request.state, "openai_rate_limit_headers", None)

    validate_openai_request(
        model,
        response_format,
        timestamp_granularities=None,
        endpoint=OpenAIEndpoint.TRANSLATIONS,
        prompt=prompt,
    )

    # Ingest audio
    try:
        ingested = await ingestion_service.ingest(
            file=file,
            url=None,
            max_bytes=OPENAI_MAX_FILE_SIZE,
        )
    except HTTPException as e:
        detail = str(e.detail)
        error_code = (
            "file_too_large" if "too large" in detail.lower() else "invalid_file_format"
        )
        raise_openai_error(
            e.status_code,
            detail,
            param="file",
            code=error_code,
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
    runtime_model_id = map_openai_runtime_model(model)
    if runtime_model_id:
        parameters[MODEL_PARAM_TRANSCRIBE] = runtime_model_id

    if prompt:
        parameters["prompt"] = prompt

    if temperature is not None:
        parameters["temperature"] = temperature

    # Generate job ID
    job_id = uuid4()

    # Upload audio to S3
    audio_uri = await storage.upload_audio(
        job_id=job_id,
        file_content=ingested.content,
        filename=ingested.filename,
    )

    # Create job
    job = await jobs_service.create_job(
        db=db,
        job_id=job_id,
        tenant_id=principal.tenant_id,
        audio_uri=audio_uri,
        parameters=parameters,
        audio_format=ingested.metadata.format,
        audio_duration=ingested.metadata.duration,
        audio_sample_rate=ingested.metadata.sample_rate,
        audio_channels=ingested.metadata.channels,
        audio_bit_depth=ingested.metadata.bit_depth,
        # Ownership tracking (M45)
        created_by_key_id=principal.id,
    )

    # Publish job.created event
    await publish_job_created(redis, job.id)

    # Track concurrent job
    await rate_limiter.increment_concurrent_jobs(principal.tenant_id)

    # Audit log
    request_id = getattr(request.state, "request_id", None)
    await audit_service.log_job_created(
        job_id=job.id,
        tenant_id=principal.tenant_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # Wait for completion (synchronous)
    result = await wait_for_job_completion(db, job)

    if result.completed:
        transcript = await storage.get_transcript(job_id)
        payload = format_openai_response(
            transcript,
            response_format,
            None,
            export_service,
            model=model,
            task="translate",
        )
        if openai_rate_headers:
            if isinstance(payload, Response):
                payload.headers.update(openai_rate_headers)
            else:
                response.headers.update(openai_rate_headers)
        return payload

    if result.failed:
        raise_openai_error(
            500,
            f"Translation failed: {job.error or 'Unknown error'}",
            error_type="server_error",
            code="processing_failed",
        )

    if result.cancelled:
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
