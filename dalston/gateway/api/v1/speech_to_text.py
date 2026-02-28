"""ElevenLabs-compatible Speech-to-Text API endpoints.

POST /v1/speech-to-text - Submit audio for transcription
GET /v1/speech-to-text/transcripts/{transcription_id} - Get transcript
GET /v1/speech-to-text/transcripts/{transcription_id}/export/{format} - Export transcript

Note: ElevenLabs uses xi-api-key header for authentication.
This is supported by the auth middleware alongside Bearer tokens.
"""

import asyncio
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

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
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.audit import AuditService
from dalston.common.events import publish_job_created
from dalston.common.models import JobStatus
from dalston.config import Settings
from dalston.gateway.dependencies import (
    RequireJobsRead,
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

router = APIRouter(prefix="/speech-to-text", tags=["speech-to-text", "elevenlabs"])


# =============================================================================
# ElevenLabs Response Models
# =============================================================================


class ElevenLabsWord(BaseModel):
    """ElevenLabs word format."""

    text: str
    start: float
    end: float
    type: str = "word"
    speaker_id: str | None = None


class ElevenLabsTranscript(BaseModel):
    """ElevenLabs transcript response format."""

    language_code: str | None = None
    language_probability: float | None = None
    text: str
    words: list[ElevenLabsWord] | None = None
    transcription_id: str


class ElevenLabsAsyncResponse(BaseModel):
    """ElevenLabs async submission response."""

    message: str = "Request processed successfully"
    request_id: str | None = None
    transcription_id: str


class ElevenLabsProcessingResponse(BaseModel):
    """Response when transcript is still processing."""

    status: str
    transcription_id: str
    message: str | None = None


# =============================================================================
# Parameter Mapping
# =============================================================================


def map_timestamps_granularity(granularity: str) -> str:
    """Map ElevenLabs timestamps_granularity to Dalston format."""
    granularity_map = {
        "none": "none",
        "word": "word",
        "character": "word",  # Dalston doesn't support character-level, use word
    }
    return granularity_map.get(granularity, "word")


# =============================================================================
# POST /v1/speech-to-text - Create Transcription
# =============================================================================


@router.post(
    "",
    response_model=ElevenLabsTranscript | ElevenLabsAsyncResponse,
    status_code=200,
    summary="Create transcription (ElevenLabs compatible)",
    description="Transcribe audio file. ElevenLabs-compatible endpoint.",
    responses={
        200: {
            "description": "Transcription result (sync) or submission confirmation (async)"
        },
        202: {"description": "Async transcription started"},
        400: {"description": "Invalid request"},
        401: {"description": "Invalid API key"},
        422: {"description": "Invalid file format"},
    },
)
async def create_transcription(
    request: Request,
    api_key: RequireJobsWriteRateLimited,
    file: UploadFile | None = File(
        default=None, description="Audio file to transcribe"
    ),
    cloud_storage_url: Annotated[
        str | None,
        Form(
            description="HTTPS URL to audio file (S3/GCS presigned URL, Google Drive, Dropbox)"
        ),
    ] = None,
    model_id: Annotated[
        str,
        Form(description="Model ID: scribe_v1, scribe_v1_experimental, or scribe_v2"),
    ] = "scribe_v1",
    language_code: Annotated[
        str | None,
        Form(
            description="Language code (ISO-639-1 or ISO-639-3) or null for auto-detect"
        ),
    ] = None,
    diarize: Annotated[
        bool,
        Form(description="Enable speaker diarization"),
    ] = False,
    num_speakers: Annotated[
        int | None,
        Form(description="Exact number of speakers (1-32)", ge=1, le=32),
    ] = None,
    timestamps_granularity: Annotated[
        str,
        Form(description="Timestamp granularity: none, word, or character"),
    ] = "word",
    tag_audio_events: Annotated[
        bool,
        Form(description="Detect audio events (laughter, etc.)"),
    ] = True,
    keyterms: Annotated[
        str | None,
        Form(
            description="JSON array of bias terms to boost recognition "
            '(e.g., \'["PostgreSQL", "Kubernetes"]\'). Max 100 terms, 50 chars each.',
        ),
    ] = None,
    webhook: Annotated[
        bool,
        Form(description="Process asynchronously with webhook callback"),
    ] = False,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    ingestion_service: AudioIngestionService = Depends(get_ingestion_service),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    audit_service: AuditService = Depends(get_audit_service),
) -> ElevenLabsTranscript | ElevenLabsAsyncResponse:
    """Create a transcription using ElevenLabs-compatible API.

    This endpoint accepts ElevenLabs parameters and translates them
    to Dalston's internal format.

    Accepts either:
    - file: Direct file upload
    - cloud_storage_url: HTTPS URL to audio file (S3/GCS presigned URL, etc.)
    """
    # Ingest audio (validates input, downloads from URL if needed, probes metadata)
    ingested = await ingestion_service.ingest(file=file, url=cloud_storage_url)

    # Map ElevenLabs parameters to Dalston parameters
    # ElevenLabs model_id (scribe_v1, scribe_v2, etc.) is treated as "auto"
    # Let the orchestrator auto-select the best engine
    dalston_language = language_code or "auto"
    dalston_speaker_detection = "diarize" if diarize else "none"
    dalston_timestamps = map_timestamps_granularity(timestamps_granularity)

    # Build parameters - no engine_transcribe means auto-select
    parameters: dict = {
        "language": dalston_language,
        "speaker_detection": dalston_speaker_detection,
        "timestamps_granularity": dalston_timestamps,
    }
    if num_speakers is not None:
        parameters["num_speakers"] = num_speakers

    # Parse and validate keyterms → vocabulary mapping
    if keyterms is not None:
        try:
            parsed_keyterms = json.loads(keyterms)
            if not isinstance(parsed_keyterms, list):
                raise HTTPException(
                    status_code=400,
                    detail="keyterms must be a JSON array of strings",
                )
            if len(parsed_keyterms) > 100:
                raise HTTPException(
                    status_code=400,
                    detail="keyterms cannot exceed 100 terms",
                )
            for term in parsed_keyterms:
                if not isinstance(term, str):
                    raise HTTPException(
                        status_code=400,
                        detail="keyterms must contain only strings",
                    )
                if len(term) > 50:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Each keyterm must be at most 50 characters, got {len(term)}",
                    )
            if parsed_keyterms:
                parameters["vocabulary"] = parsed_keyterms
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON in keyterms: {e}",
            ) from e

    # Generate job ID upfront so we can upload to the correct S3 path
    job_id = uuid4()

    # Upload audio to S3
    storage = StorageService(settings)
    audio_uri = await storage.upload_audio(
        job_id=job_id,
        file_content=ingested.content,
        filename=ingested.filename,
    )

    # Create job in database (now includes audio metadata from probing)
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

    # Publish job.created event for orchestrator
    await publish_job_created(redis, job.id)

    # Track concurrent job for rate limiting
    await rate_limiter.increment_concurrent_jobs(api_key.tenant_id)

    # Audit log job creation
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

    # If async/webhook mode, return immediately
    if webhook:
        return ElevenLabsAsyncResponse(
            message="Request processed successfully",
            transcription_id=str(job.id),
        )

    # For sync mode, wait for completion (with timeout)
    max_wait_seconds = 300  # 5 minute timeout for sync
    poll_interval = 1.0
    elapsed = 0.0

    while elapsed < max_wait_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        # Expire cached job object and refresh from database
        db.expire(job)
        await db.refresh(job)

        if job.status == JobStatus.COMPLETED.value:
            # Fetch transcript and return ElevenLabs format
            transcript = await storage.get_transcript(job_id)
            return _format_elevenlabs_response(job_id, transcript)

        if job.status == JobStatus.FAILED.value:
            raise HTTPException(
                status_code=500,
                detail=f"Transcription failed: {job.error or 'Unknown error'}",
            )

        if job.status == JobStatus.CANCELLED.value:
            raise HTTPException(status_code=400, detail="Transcription was cancelled")

    # Timeout - return async response
    raise HTTPException(
        status_code=408,
        detail="Transcription timeout. Use webhook=true for long files.",
    )


# =============================================================================
# GET /v1/speech-to-text/transcripts/{id} - Get Transcript
# =============================================================================


@router.get(
    "/transcripts/{transcription_id}",
    response_model=ElevenLabsTranscript | ElevenLabsProcessingResponse,
    summary="Get transcript (ElevenLabs compatible)",
    description="Retrieve transcription result. ElevenLabs-compatible endpoint.",
    responses={
        200: {"description": "Transcription result or processing status"},
        404: {"description": "Transcription not found"},
    },
)
async def get_transcript(
    transcription_id: UUID,
    api_key: RequireJobsRead,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> ElevenLabsTranscript | ElevenLabsProcessingResponse:
    """Get transcription result in ElevenLabs format.

    Returns the transcript if completed, or processing status if still running.
    """
    job = await jobs_service.get_job(db, transcription_id, tenant_id=api_key.tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Transcription not found")

    # If not completed, return status
    if job.status != JobStatus.COMPLETED.value:
        status_map = {
            JobStatus.PENDING.value: "pending",
            JobStatus.RUNNING.value: "processing",
            JobStatus.FAILED.value: "failed",
            JobStatus.CANCELLING.value: "cancelling",
            JobStatus.CANCELLED.value: "cancelled",
        }
        return ElevenLabsProcessingResponse(
            status=status_map.get(job.status, job.status),
            transcription_id=str(transcription_id),
            message=job.error if job.status == JobStatus.FAILED.value else None,
        )

    # Fetch transcript from storage
    storage = StorageService(settings)
    transcript = await storage.get_transcript(transcription_id)

    return _format_elevenlabs_response(transcription_id, transcript)


def _format_elevenlabs_response(
    transcription_id: UUID,
    transcript: dict[str, Any],
) -> ElevenLabsTranscript:
    """Format Dalston transcript as ElevenLabs response."""
    # Extract words in ElevenLabs format
    # First check top-level words, then extract from segments
    words = None
    word_list = transcript.get("words")

    if not word_list:
        # Extract words from segments
        word_list = []
        for segment in transcript.get("segments", []):
            segment_words = segment.get("words", [])
            speaker = segment.get("speaker")
            for w in segment_words:
                w_copy = dict(w)
                if speaker and not w_copy.get("speaker"):
                    w_copy["speaker"] = speaker
                word_list.append(w_copy)

    if word_list:
        words = [
            ElevenLabsWord(
                text=w.get("text", w.get("word", "")),
                start=w.get("start", 0),
                end=w.get("end", 0),
                type="word",
                speaker_id=w.get("speaker") or w.get("speaker_id"),
            )
            for w in word_list
        ]

    # Get language from metadata (matches native API structure)
    metadata = transcript.get("metadata", {})
    language_code = (
        metadata.get("language")
        or transcript.get("language_code")
        or transcript.get("language")
    )
    language_probability = metadata.get("language_confidence") or transcript.get(
        "language_probability"
    )

    return ElevenLabsTranscript(
        language_code=language_code,
        language_probability=language_probability,
        text=transcript.get("text", ""),
        words=words,
        transcription_id=str(transcription_id),
    )


# =============================================================================
# GET /v1/speech-to-text/transcripts/{id}/export/{format} - Export Transcript
# =============================================================================


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
