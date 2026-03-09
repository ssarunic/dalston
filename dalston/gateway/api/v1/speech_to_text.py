"""ElevenLabs-compatible Speech-to-Text API endpoints.

POST /v1/speech-to-text - Submit audio for transcription
GET /v1/speech-to-text/transcripts/{transcription_id} - Get transcript
GET /v1/speech-to-text/transcripts/{transcription_id}/export/{format} - Export transcript

Note: ElevenLabs uses xi-api-key header for authentication.
This is supported by the auth middleware alongside Bearer tokens.
"""

import json
import re
from json import JSONDecodeError
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
from dalston.gateway.api.v1.elevenlabs_stt import (
    ELEVENLABS_MAX_CLOUD_URL_BYTES,
    ELEVENLABS_MAX_UPLOAD_BYTES,
    ElevenLabsEndpoint,
    ensure_field_location_supported,
    ensure_model_supported,
    validate_elevenlabs_keyterms,
)
from dalston.gateway.dependencies import (
    get_audit_service,
    get_db,
    get_export_service,
    get_ingestion_service,
    get_jobs_service,
    get_principal,
    get_principal_with_job_rate_limit,
    get_rate_limiter,
    get_redis,
    get_security_manager,
    get_settings,
    get_storage_service,
)
from dalston.gateway.error_codes import Err
from dalston.gateway.security.exceptions import ResourceNotFoundError
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.ingestion import AudioIngestionService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.polling import wait_for_job_completion
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
    type: str
    speaker_id: str | None = None
    logprob: float | None = None
    characters: list[dict[str, Any]] | None = None


class ElevenLabsTranscript(BaseModel):
    """ElevenLabs transcript response format."""

    language_code: str | None = None
    language_probability: float | None = None
    text: str
    words: list[ElevenLabsWord] | None = None
    entities: list[dict[str, Any]] | None = None
    additional_formats: list[dict[str, Any]] | None = None
    transcription_id: str


class ElevenLabsTranscriptChunk(BaseModel):
    """ElevenLabs multichannel transcript chunk."""

    language_code: str
    language_probability: float
    text: str
    words: list[ElevenLabsWord]
    channel_index: int | None = None
    additional_formats: list[dict[str, Any]] | None = None
    transcription_id: str | None = None
    entities: list[dict[str, Any]] | None = None


class ElevenLabsMultiChannelTranscript(BaseModel):
    """ElevenLabs multichannel transcript response."""

    transcripts: list[ElevenLabsTranscriptChunk]
    transcription_id: str


class ElevenLabsAsyncResponse(BaseModel):
    """ElevenLabs async submission response."""

    message: str = "Request processed successfully"
    request_id: str | None = None
    transcription_id: str


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
    response_model=(
        ElevenLabsTranscript
        | ElevenLabsMultiChannelTranscript
        | ElevenLabsAsyncResponse
    ),
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
    response: Response,
    principal: Annotated[Principal, Depends(get_principal_with_job_rate_limit)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    enable_logging: Annotated[
        bool,
        Query(
            description=(
                "Whether audio/log data may be retained for provider-side diagnostics."
            )
        ),
    ] = True,
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
    file_format: Annotated[
        str | None,
        Form(description="Expected file format hint (docs-backed field inventory)."),
    ] = None,
    webhook_id: Annotated[
        str | None,
        Form(description="Registered webhook destination id."),
    ] = None,
    webhook_metadata: Annotated[
        str | None,
        Form(description="JSON metadata for webhook delivery context."),
    ] = None,
    entity_detection: Annotated[
        bool | None,
        Form(description="Enable entity extraction (phase 2 wiring)."),
    ] = None,
    additional_formats: Annotated[
        str | None,
        Form(description="JSON array of additional output formats."),
    ] = None,
    temperature: Annotated[
        float | None,
        Form(description="Decoding temperature control."),
    ] = None,
    seed: Annotated[
        int | None,
        Form(description="Deterministic decoding seed."),
    ] = None,
    use_multi_channel: Annotated[
        bool | None,
        Form(description="Enable multi-channel transcript shape."),
    ] = None,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
    ingestion_service: AudioIngestionService = Depends(get_ingestion_service),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    audit_service: AuditService = Depends(get_audit_service),
    export_service: ExportService = Depends(get_export_service),
    storage: StorageService = Depends(get_storage_service),
) -> ElevenLabsTranscript | ElevenLabsMultiChannelTranscript | ElevenLabsAsyncResponse:
    """Create a transcription using ElevenLabs-compatible API.

    This endpoint accepts ElevenLabs parameters and translates them
    to Dalston's internal format.

    Accepts either:
    - file: Direct file upload
    - cloud_storage_url: HTTPS URL to audio file (S3/GCS presigned URL, etc.)
    """
    security_manager.require_permission(principal, Permission.JOB_CREATE)
    request_id = getattr(request.state, "request_id", None)

    # Enforce docs-backed field location contract (query vs multipart).
    for query_field in request.query_params.keys():
        ensure_field_location_supported(ElevenLabsEndpoint.BATCH, "query", query_field)
    form_payload = await request.form()
    for form_field in form_payload.keys():
        ensure_field_location_supported(
            ElevenLabsEndpoint.BATCH, "multipart", form_field
        )

    ensure_model_supported(model_id, ElevenLabsEndpoint.BATCH)

    # Explicit rejects for documented fields not yet wired end-to-end.
    if file_format is not None:
        raise HTTPException(
            status_code=400,
            detail="file_format is not yet supported on this deployment.",
        )

    # Ingest audio (validates input, downloads from URL if needed, probes metadata)
    ingested = await ingestion_service.ingest(
        file=file,
        url=cloud_storage_url,
        max_bytes=(
            ELEVENLABS_MAX_CLOUD_URL_BYTES
            if cloud_storage_url is not None
            else ELEVENLABS_MAX_UPLOAD_BYTES
        ),
    )

    # Map ElevenLabs parameters to Dalston parameters
    dalston_language = language_code or "auto"
    enable_multi_channel = bool(use_multi_channel)
    if enable_multi_channel and ingested.metadata.channels > 5:
        raise HTTPException(
            status_code=400,
            detail=(
                "use_multi_channel supports a maximum of 5 channels, got "
                f"{ingested.metadata.channels}."
            ),
        )
    if enable_multi_channel and diarize:
        raise HTTPException(
            status_code=400,
            detail="use_multi_channel cannot be combined with diarize=true.",
        )

    dalston_speaker_detection = (
        "per_channel" if enable_multi_channel else ("diarize" if diarize else "none")
    )
    dalston_timestamps = map_timestamps_granularity(timestamps_granularity)
    parsed_webhook_metadata: dict[str, Any] | None = None
    if webhook_metadata is not None:
        try:
            metadata_value = json.loads(webhook_metadata)
        except JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON in webhook_metadata: {e}",
            ) from e
        if not isinstance(metadata_value, dict):
            raise HTTPException(
                status_code=400,
                detail="webhook_metadata must be a JSON object",
            )
        parsed_webhook_metadata = metadata_value

    parsed_additional_formats: list[str] | None = None
    if additional_formats is not None:
        try:
            formats_value = json.loads(additional_formats)
        except JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON in additional_formats: {e}",
            ) from e
        if not isinstance(formats_value, list) or not all(
            isinstance(fmt, str) for fmt in formats_value
        ):
            raise HTTPException(
                status_code=400,
                detail="additional_formats must be a JSON array of strings",
            )
        parsed_additional_formats = [fmt.lower() for fmt in formats_value]

    # Build parameters - use configured default model
    parameters: dict = {
        "language": dalston_language,
        "speaker_detection": dalston_speaker_detection,
        "timestamps_granularity": dalston_timestamps,
        "enable_logging": enable_logging,
        "elevenlabs_model_id": model_id,
        "elevenlabs_webhook_enabled": webhook,
        "request_id": request_id,
        "use_multi_channel": enable_multi_channel,
        "engine_transcribe": settings.default_model,
    }
    if enable_multi_channel:
        parameters["num_channels"] = ingested.metadata.channels
    if num_speakers is not None:
        parameters["num_speakers"] = num_speakers
    if entity_detection is not None:
        parameters["entity_detection"] = entity_detection
    if temperature is not None:
        parameters["temperature"] = temperature
    if seed is not None:
        parameters["seed"] = seed
    if parsed_additional_formats:
        parameters["additional_formats"] = parsed_additional_formats
    if webhook_id is not None:
        try:
            parameters["elevenlabs_webhook_id"] = str(UUID(webhook_id))
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail="webhook_id must be a valid UUID",
            ) from e
    if parsed_webhook_metadata is not None:
        parameters["elevenlabs_webhook_metadata"] = parsed_webhook_metadata

    # Parse and validate keyterms → vocabulary mapping
    if keyterms is not None:
        try:
            parsed_keyterms = json.loads(keyterms)
            if not isinstance(parsed_keyterms, list):
                raise HTTPException(
                    status_code=400,
                    detail=Err.KEYTERMS_MUST_BE_ARRAY,
                )
            if len(parsed_keyterms) > 100:
                raise HTTPException(
                    status_code=400,
                    detail=Err.KEYTERMS_EXCEED_LIMIT,
                )
            for term in parsed_keyterms:
                if not isinstance(term, str):
                    raise HTTPException(
                        status_code=400,
                        detail=Err.KEYTERMS_MUST_BE_STRINGS,
                    )
            validate_elevenlabs_keyterms(parsed_keyterms)
            if parsed_keyterms:
                parameters["vocabulary"] = parsed_keyterms
        except JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=Err.KEYTERMS_INVALID_JSON.format(error=e),
            ) from e

    # Generate job ID upfront so we can upload to the correct S3 path
    job_id = uuid4()

    # Upload audio to S3
    audio_uri = await storage.upload_audio(
        job_id=job_id,
        file_content=ingested.content,
        filename=ingested.filename,
    )

    # Create job in database (now includes audio metadata from probing)
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

    # Publish job.created event for orchestrator
    await publish_job_created(redis, job.id, request_id=request_id)

    # Track concurrent job for rate limiting
    concurrent_jobs = await rate_limiter.check_concurrent_jobs(principal.tenant_id)
    await rate_limiter.increment_concurrent_jobs(principal.tenant_id)
    current_concurrency = (concurrent_jobs.limit - concurrent_jobs.remaining) + 1
    response.headers["current-concurrent-requests"] = str(max(0, current_concurrency))
    response.headers["maximum-concurrent-requests"] = str(concurrent_jobs.limit)

    # Audit log job creation
    await audit_service.log_job_created(
        job_id=job.id,
        tenant_id=principal.tenant_id,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        correlation_id=request_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    # If async/webhook mode, return immediately
    if webhook:
        return ElevenLabsAsyncResponse(
            message="Request processed successfully",
            request_id=request_id,
            transcription_id=str(job.id),
        )

    # For sync mode, wait for completion (with timeout)
    result = await wait_for_job_completion(db, job)

    if result.completed:
        # Fetch transcript and return ElevenLabs format
        transcript = await storage.get_transcript(job_id)
        return _format_elevenlabs_response(
            job_id,
            transcript,
            export_service=export_service,
            additional_formats=parsed_additional_formats,
            entities_enabled=bool(entity_detection),
            use_multi_channel=enable_multi_channel,
        )

    if result.failed:
        raise HTTPException(
            status_code=500,
            detail=Err.TRANSCRIPTION_FAILED.format(error=job.error or "Unknown error"),
        )

    if result.cancelled:
        raise HTTPException(status_code=400, detail=Err.TRANSCRIPTION_CANCELLED)

    # Timeout
    raise HTTPException(
        status_code=408,
        detail=Err.TRANSCRIPTION_TIMEOUT,
    )


# =============================================================================
# GET /v1/speech-to-text/transcripts/{id} - Get Transcript
# =============================================================================


@router.get(
    "/transcripts/{transcription_id}",
    response_model=ElevenLabsTranscript | ElevenLabsMultiChannelTranscript,
    summary="Get transcript (ElevenLabs compatible)",
    description="Retrieve transcription result. ElevenLabs-compatible endpoint.",
    responses={
        200: {"description": "Completed transcription result"},
        404: {"description": "Transcription not found"},
    },
)
async def get_transcript(
    transcription_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
    export_service: ExportService = Depends(get_export_service),
    storage: StorageService = Depends(get_storage_service),
) -> ElevenLabsTranscript | ElevenLabsMultiChannelTranscript:
    """Get transcription result in ElevenLabs format.

    Returns the transcript if completed, or processing status if still running.
    Enforces ownership: non-admin principals can only access their own transcripts.
    """
    job = await jobs_service.get_job_authorized(
        db, transcription_id, principal, security_manager
    )
    if job is None:
        raise HTTPException(status_code=404, detail=Err.TRANSCRIPTION_NOT_FOUND)

    # ElevenLabs polling contract: do not emit Dalston-only processing payloads.
    # While still pending/running, return 404 with Retry-After.
    if job.status != JobStatus.COMPLETED.value:
        if job.status in {
            JobStatus.PENDING.value,
            JobStatus.RUNNING.value,
            JobStatus.CANCELLING.value,
        }:
            raise HTTPException(
                status_code=404,
                detail=Err.TRANSCRIPTION_NOT_FOUND,
                headers={"Retry-After": "2"},
            )
        raise HTTPException(
            status_code=404,
            detail=Err.TRANSCRIPTION_NOT_FOUND,
        )

    # Fetch transcript from storage
    transcript = await storage.get_transcript(transcription_id)
    params = job.parameters if isinstance(job.parameters, dict) else {}
    additional_formats = params.get("additional_formats")
    if not isinstance(additional_formats, list):
        additional_formats = None

    return _format_elevenlabs_response(
        transcription_id,
        transcript,
        export_service=export_service,
        additional_formats=additional_formats,
        entities_enabled=bool(params.get("entity_detection")),
        use_multi_channel=bool(params.get("use_multi_channel")),
    )


def _format_elevenlabs_response(
    transcription_id: UUID,
    transcript: dict[str, Any],
    *,
    export_service: ExportService | None = None,
    additional_formats: list[str] | None = None,
    entities_enabled: bool = False,
    use_multi_channel: bool = False,
) -> ElevenLabsTranscript | ElevenLabsMultiChannelTranscript:
    """Format Dalston transcript as ElevenLabs response."""
    if use_multi_channel:
        return _format_elevenlabs_multichannel_response(
            transcription_id=transcription_id,
            transcript=transcript,
            export_service=export_service,
            additional_formats=additional_formats,
            entities_enabled=entities_enabled,
        )

    word_list = _extract_word_dicts(transcript)
    words = _build_elevenlabs_words(word_list)
    language_code, language_probability = _resolve_language_fields(transcript)

    entities: list[dict[str, Any]] | None = None
    if entities_enabled:
        raw_entities = transcript.get("entities", transcript.get("pii_entities"))
        if isinstance(raw_entities, list):
            entities = [e for e in raw_entities if isinstance(e, dict)]

    rendered_additional_formats: list[dict[str, Any]] | None = None
    if export_service is not None and additional_formats:
        rendered_additional_formats = _render_additional_formats(
            transcript=transcript,
            export_service=export_service,
            requested_formats=additional_formats,
        )

    return ElevenLabsTranscript(
        language_code=language_code,
        language_probability=language_probability,
        text=str(transcript.get("text", "")),
        words=words,
        entities=entities,
        additional_formats=rendered_additional_formats,
        transcription_id=str(transcription_id),
    )


def _resolve_language_fields(transcript: dict[str, Any]) -> tuple[str, float]:
    """Resolve required language fields for ElevenLabs SDK compatibility."""
    metadata = transcript.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    language_code = (
        metadata.get("language")
        or transcript.get("language_code")
        or transcript.get("language")
    )
    if not isinstance(language_code, str) or not language_code:
        language_code = "und"

    language_probability = metadata.get("language_confidence") or transcript.get(
        "language_probability"
    )
    if not isinstance(language_probability, int | float):
        language_probability = 0.0

    return language_code, float(language_probability)


def _resolve_word_type(word: dict[str, Any]) -> str:
    explicit = word.get("type")
    if isinstance(explicit, str) and explicit:
        return explicit
    text = str(word.get("text", word.get("word", "")))
    if text and text.isspace():
        return "spacing"
    return "word"


def _resolve_word_characters(word: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = word.get("characters")
    if not isinstance(raw, list):
        return None

    chars: list[dict[str, Any]] = []
    for ch in raw:
        if not isinstance(ch, dict):
            continue
        char_text = ch.get("text", ch.get("char"))
        if not isinstance(char_text, str):
            continue
        chars.append(
            {
                "text": char_text,
                "start": float(ch.get("start", 0)),
                "end": float(ch.get("end", 0)),
            }
        )
    return chars or None


def _extract_word_dicts(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract raw word dicts from top-level transcript or segment payloads."""
    top_level_words = transcript.get("words")
    if isinstance(top_level_words, list) and top_level_words:
        return [w for w in top_level_words if isinstance(w, dict)]

    word_list: list[dict[str, Any]] = []
    for segment in transcript.get("segments", []):
        if not isinstance(segment, dict):
            continue
        segment_words = segment.get("words")
        if not isinstance(segment_words, list):
            continue
        speaker = segment.get("speaker")
        for word in segment_words:
            if not isinstance(word, dict):
                continue
            w_copy = dict(word)
            if (
                isinstance(speaker, str)
                and speaker
                and not w_copy.get("speaker")
                and not w_copy.get("speaker_id")
            ):
                w_copy["speaker"] = speaker
            word_list.append(w_copy)
    return word_list


def _build_elevenlabs_words(word_list: list[dict[str, Any]]) -> list[ElevenLabsWord]:
    """Build ElevenLabs word payload with SDK-compatible required fields."""
    words: list[ElevenLabsWord] = []
    for word in word_list:
        logprob_value = word.get("logprob")
        if not isinstance(logprob_value, int | float):
            logprob_value = 0.0
        words.append(
            ElevenLabsWord(
                text=str(word.get("text", word.get("word", ""))),
                start=float(word.get("start", 0)),
                end=float(word.get("end", 0)),
                type=_resolve_word_type(word),
                speaker_id=word.get("speaker") or word.get("speaker_id"),
                logprob=float(logprob_value),
                characters=_resolve_word_characters(word),
            )
        )
    return words


def _build_speaker_channel_map(transcript: dict[str, Any]) -> dict[str, int]:
    """Map speaker IDs to channel indices for per-channel transcript formatting."""
    mapping: dict[str, int] = {}
    raw_speakers = transcript.get("speakers")
    if not isinstance(raw_speakers, list):
        return mapping

    for speaker in raw_speakers:
        if not isinstance(speaker, dict):
            continue
        speaker_id = speaker.get("id")
        channel = speaker.get("channel")
        if isinstance(speaker_id, str) and isinstance(channel, int):
            mapping[speaker_id] = channel
    return mapping


def _resolve_segment_channel(
    segment: dict[str, Any],
    speaker_channel_map: dict[str, int],
) -> int:
    """Resolve a segment channel from explicit metadata, speaker map, or naming."""
    channel = segment.get("channel")
    if isinstance(channel, int):
        return channel

    speaker = segment.get("speaker")
    if isinstance(speaker, str):
        mapped = speaker_channel_map.get(speaker)
        if mapped is not None:
            return mapped

        match = re.match(r"^SPEAKER_(\d+)$", speaker)
        if match:
            return int(match.group(1))

    return 0


def _filter_entities_for_channel(
    entities: list[dict[str, Any]],
    channel: int,
    speaker_channel_map: dict[str, int],
) -> list[dict[str, Any]] | None:
    """Filter transcript-level entities down to one channel transcript."""
    filtered: list[dict[str, Any]] = []
    for entity in entities:
        explicit_channel = entity.get("channel")
        if isinstance(explicit_channel, int):
            if explicit_channel == channel:
                filtered.append(entity)
            continue

        speaker = entity.get("speaker")
        if not isinstance(speaker, str):
            continue

        mapped_channel = speaker_channel_map.get(speaker)
        if mapped_channel is not None:
            if mapped_channel == channel:
                filtered.append(entity)
            continue

        if speaker.endswith(f"ch{channel}") or speaker == f"SPEAKER_{channel:02d}":
            filtered.append(entity)

    return filtered or None


def _format_elevenlabs_multichannel_response(
    transcription_id: UUID,
    transcript: dict[str, Any],
    *,
    export_service: ExportService | None = None,
    additional_formats: list[str] | None = None,
    entities_enabled: bool = False,
) -> ElevenLabsMultiChannelTranscript:
    """Format per-channel transcript payload for ElevenLabs multichannel contract."""
    language_code, language_probability = _resolve_language_fields(transcript)
    speaker_channel_map = _build_speaker_channel_map(transcript)

    segments_by_channel: dict[int, list[dict[str, Any]]] = {}
    raw_segments = transcript.get("segments")
    if isinstance(raw_segments, list):
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue
            channel = _resolve_segment_channel(raw_segment, speaker_channel_map)
            segments_by_channel.setdefault(channel, []).append(raw_segment)

    raw_entities: list[dict[str, Any]] = []
    if entities_enabled:
        entities = transcript.get("entities", transcript.get("pii_entities"))
        if isinstance(entities, list):
            raw_entities = [entity for entity in entities if isinstance(entity, dict)]

    transcripts: list[ElevenLabsTranscriptChunk] = []
    if not segments_by_channel:
        single_words = _build_elevenlabs_words(_extract_word_dicts(transcript))
        transcripts.append(
            ElevenLabsTranscriptChunk(
                language_code=language_code,
                language_probability=language_probability,
                text=str(transcript.get("text", "")),
                words=single_words,
                channel_index=0,
                transcription_id=str(transcription_id),
                entities=raw_entities or None,
            )
        )
    else:
        for channel_index in sorted(segments_by_channel):
            channel_segments = sorted(
                segments_by_channel[channel_index],
                key=lambda segment: float(segment.get("start", 0)),
            )
            text_chunks = [
                str(segment.get("text", "")).strip()
                for segment in channel_segments
                if str(segment.get("text", "")).strip()
            ]
            channel_text = " ".join(text_chunks)

            channel_words_raw: list[dict[str, Any]] = []
            for segment in channel_segments:
                speaker = segment.get("speaker")
                segment_words = segment.get("words")
                if not isinstance(segment_words, list):
                    continue
                for word in segment_words:
                    if not isinstance(word, dict):
                        continue
                    w_copy = dict(word)
                    if (
                        isinstance(speaker, str)
                        and speaker
                        and not w_copy.get("speaker")
                        and not w_copy.get("speaker_id")
                    ):
                        w_copy["speaker"] = speaker
                    channel_words_raw.append(w_copy)

            rendered_additional_formats: list[dict[str, Any]] | None = None
            if export_service is not None and additional_formats:
                channel_transcript = dict(transcript)
                channel_transcript["text"] = channel_text
                channel_transcript["segments"] = channel_segments
                channel_transcript["words"] = channel_words_raw
                rendered_additional_formats = _render_additional_formats(
                    transcript=channel_transcript,
                    export_service=export_service,
                    requested_formats=additional_formats,
                )

            transcripts.append(
                ElevenLabsTranscriptChunk(
                    language_code=language_code,
                    language_probability=language_probability,
                    text=channel_text,
                    words=_build_elevenlabs_words(channel_words_raw),
                    channel_index=channel_index,
                    additional_formats=rendered_additional_formats,
                    transcription_id=str(transcription_id),
                    entities=_filter_entities_for_channel(
                        raw_entities,
                        channel_index,
                        speaker_channel_map,
                    ),
                )
            )

    return ElevenLabsMultiChannelTranscript(
        transcripts=transcripts,
        transcription_id=str(transcription_id),
    )


def _render_additional_formats(
    transcript: dict[str, Any],
    export_service: ExportService,
    requested_formats: list[str],
) -> list[dict[str, Any]]:
    """Render additional export formats inline for ElevenLabs responses."""
    rendered: list[dict[str, Any]] = []
    for requested in requested_formats:
        export_format = export_service.validate_format(requested)

        if export_format == "srt":
            content: Any = export_service.export_srt(transcript)
        elif export_format == "vtt":
            content = export_service.export_vtt(transcript)
        elif export_format == "txt":
            content = export_service.export_txt(transcript)
        else:
            content = transcript

        rendered.append({"format": requested, "content": content})
    return rendered


# =============================================================================
# DELETE /v1/speech-to-text/transcripts/{id} - Delete Transcript
# =============================================================================


@router.delete(
    "/transcripts/{transcription_id}",
    status_code=204,
    summary="Delete transcript (ElevenLabs compatible)",
    description="Delete transcription job and artifacts via ElevenLabs-compatible alias.",
    responses={
        204: {"description": "Transcript deleted"},
        404: {"description": "Transcription not found"},
        409: {"description": "Transcription is not in a terminal state"},
    },
)
async def delete_transcript(
    request: Request,
    transcription_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
    audit_service: AuditService = Depends(get_audit_service),
    storage: StorageService = Depends(get_storage_service),
) -> Response:
    """Delete transcript alias mapped to the native job deletion service."""
    request_id = getattr(request.state, "request_id", None)

    try:
        job = await jobs_service.delete_job_authorized(
            db,
            transcription_id,
            principal,
            security_manager,
            audit_service=audit_service,
            correlation_id=request_id,
            ip_address=request.client.host if request.client else None,
        )
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=404, detail=Err.TRANSCRIPTION_NOT_FOUND
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if job is None:
        raise HTTPException(status_code=404, detail=Err.TRANSCRIPTION_NOT_FOUND)

    # Best-effort artifact cleanup after DB delete
    try:
        await storage.delete_job_artifacts(transcription_id)
    except Exception:
        pass

    return Response(status_code=204)


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
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
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
    jobs_service: JobsService = Depends(get_jobs_service),
    export_service: ExportService = Depends(get_export_service),
    storage: StorageService = Depends(get_storage_service),
) -> Response:
    """Export transcript in specified format (ElevenLabs compatible).

    Supported formats:
    - srt: SubRip subtitle format
    - webvtt: WebVTT subtitle format
    - txt: Plain text
    - json: Full transcript JSON

    Note: ElevenLabs uses 'webvtt' while Dalston native uses 'vtt'.
    Both are supported.

    Enforces ownership: non-admin principals can only access their own transcripts.
    """
    # Validate format
    export_format = export_service.validate_format(format)

    # Get job with authorization (transcription_id maps to job_id internally)
    job = await jobs_service.get_job_authorized(
        db, transcription_id, principal, security_manager
    )
    if job is None:
        raise HTTPException(status_code=404, detail=Err.TRANSCRIPTION_NOT_FOUND)

    # Check job is completed
    if job.status != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=Err.TRANSCRIPTION_NOT_COMPLETED.format(status=job.status),
        )

    # Fetch transcript from S3
    transcript = await storage.get_transcript(job.id)

    # Generate and return export response
    return export_service.create_export_response(
        transcript=transcript,
        export_format=export_format,
        include_speakers=include_speakers,
        max_line_length=max_line_length,
        max_lines=max_lines,
    )
