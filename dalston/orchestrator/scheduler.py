"""Task scheduler for pushing ready tasks to engine queues.

Handles:
- Writing task metadata to Redis for engine lookup
- Writing task input.json to S3
- Pushing task IDs to engine-specific Redis queues
- Validating engine capabilities against job requirements (M29)
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
import structlog.contextvars
from redis.asyncio import Redis

import dalston.telemetry
from dalston.common.models import Task
from dalston.common.pipeline_types import AudioMedia, TaskInputData
from dalston.common.s3 import get_s3_client
from dalston.config import Settings
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog, get_catalog
from dalston.orchestrator.exceptions import (
    CatalogValidationError,
    EngineCapabilityError,
    EngineInfo,
    EngineUnavailableError,
    ErrorDetails,
    build_engine_suggestion,
)
from dalston.orchestrator.registry import BatchEngineRegistry

logger = structlog.get_logger()

# Redis key patterns
TASK_METADATA_KEY = "dalston:task:{task_id}"
ENGINE_QUEUE_KEY = "dalston:queue:{engine_id}"

# Timeout calculation constants (M30)
MIN_TIMEOUT_S = 60  # Minimum timeout for any task
DEFAULT_RTF = 1.0  # Fallback RTF if not specified
TIMEOUT_SAFETY_FACTOR = 3.0  # Multiply estimated time by this factor


def _build_engine_info(
    entry: CatalogEntry,
    running_ids: set[str],
    unhealthy_ids: set[str],
) -> EngineInfo:
    """Build EngineInfo from catalog entry with runtime status."""
    if entry.engine_id in running_ids:
        status = "running"
    elif entry.engine_id in unhealthy_ids:
        status = "unhealthy"
    else:
        status = "available"

    return EngineInfo(
        id=entry.engine_id,
        languages=entry.capabilities.languages,
        supports_word_timestamps=entry.capabilities.supports_word_timestamps,
        status=status,
    )


async def _build_error_details(
    catalog: EngineCatalog,
    registry: BatchEngineRegistry,
    stage: str,
    language: str | None = None,
    word_timestamps: bool | None = None,
) -> ErrorDetails:
    """Build detailed error context for validation errors (M30).

    Args:
        catalog: Engine catalog
        registry: Batch engine registry
        stage: Pipeline stage
        language: Requested language (if any)
        word_timestamps: Whether word timestamps were requested

    Returns:
        ErrorDetails with available engines and suggestions
    """
    # Get running engine states
    running_engines = await registry.get_engines()
    running_ids = {e.engine_id for e in running_engines if e.is_available}
    unhealthy_ids = {e.engine_id for e in running_engines if not e.is_available}

    # Get all engines for this stage from catalog
    stage_engines = catalog.get_engines_for_stage(stage)
    available_engines = [
        _build_engine_info(e, running_ids, unhealthy_ids) for e in stage_engines
    ]

    # Build required dict
    required: dict[str, str | bool] = {"stage": stage}
    if language:
        required["language"] = language
    if word_timestamps is not None:
        required["word_timestamps"] = word_timestamps

    # Build suggestion
    suggestion = build_engine_suggestion(stage, language, available_engines)

    return ErrorDetails(
        required=required,
        available_engines=available_engines,
        suggestion=suggestion,
    )


def calculate_task_timeout(
    audio_duration_s: float | None,
    rtf_gpu: float | None = None,
    rtf_cpu: float | None = None,
    use_gpu: bool = True,
) -> int:
    """Calculate task timeout based on audio duration and engine RTF.

    Uses Real-Time Factor (RTF) to estimate processing time. RTF of 0.1 means
    the engine processes 10x faster than real-time (e.g., 60s audio in 6s).

    The timeout includes a safety factor to account for:
    - Model loading on cold start
    - I/O overhead (S3 upload/download)
    - Queue wait time variance

    Args:
        audio_duration_s: Audio duration in seconds. If None, returns default.
        rtf_gpu: Real-time factor on GPU (e.g., 0.05 = 20x faster than realtime)
        rtf_cpu: Real-time factor on CPU (e.g., 0.8 = 1.25x faster than realtime)
        use_gpu: Whether to prefer GPU RTF (falls back to CPU if GPU not available)

    Returns:
        Timeout in seconds (integer)

    Examples:
        >>> calculate_task_timeout(3600, rtf_gpu=0.05)  # 1h audio, fast GPU
        540  # 3600 * 0.05 * 3 = 540s (9 min)

        >>> calculate_task_timeout(60, rtf_cpu=0.8)  # 1 min audio, slow CPU
        144  # 60 * 0.8 * 3 = 144s (but min 60s)
    """
    if audio_duration_s is None or audio_duration_s <= 0:
        # Default timeout for unknown duration (e.g., prepare stage)
        return MIN_TIMEOUT_S * 5  # 5 minutes

    # Select RTF based on hardware preference
    rtf = None
    if use_gpu and rtf_gpu is not None:
        rtf = rtf_gpu
    elif rtf_cpu is not None:
        rtf = rtf_cpu
    elif rtf_gpu is not None:
        rtf = rtf_gpu

    if rtf is None or rtf <= 0:
        rtf = DEFAULT_RTF

    # Calculate estimated processing time with safety factor
    estimated_s = audio_duration_s * rtf * TIMEOUT_SAFETY_FACTOR

    # Ensure minimum timeout
    return max(int(estimated_s), MIN_TIMEOUT_S)


async def queue_task(
    redis: Redis,
    task: Task,
    settings: Settings,
    registry: BatchEngineRegistry,
    previous_outputs: dict[str, Any] | None = None,
    audio_metadata: dict[str, Any] | None = None,
    catalog: EngineCatalog | None = None,
) -> None:
    """Queue a task for execution by its engine.

    Steps:
    1. Validate catalog (does any engine support this stage + requirements?)
    2. Check engine availability (fail fast if engine not running)
    3. Validate capabilities (does running engine support job requirements?)
    4. Store task metadata in Redis hash (for engine lookup)
    5. Write task input.json to S3
    6. Push task_id to engine queue

    Args:
        redis: Async Redis client
        task: Task to queue
        settings: Application settings (for S3 bucket)
        registry: Batch engine registry for availability checks
        previous_outputs: Outputs from dependency tasks (keyed by stage)
        audio_metadata: Audio file metadata (format, duration, sample_rate, channels)
        catalog: Engine catalog for validation (uses singleton if not provided)

    Raises:
        CatalogValidationError: If no engine in catalog supports the requirements
        EngineUnavailableError: If the required engine is not running
        EngineCapabilityError: If running engine doesn't support job requirements
    """
    task_id_str = str(task.id)
    job_id_str = str(task.job_id)

    # Get language from task config (if present)
    # Normalize "auto" to None - it means auto-detect, not a language requirement
    language = task.config.get("language") if task.config else None
    if language and language.lower() == "auto":
        language = None

    # 1. Catalog check - does any engine in catalog support this?
    if catalog is None:
        catalog = get_catalog()

    # Get word_timestamps requirement from config
    word_timestamps = task.config.get("word_timestamps") if task.config else None

    if language and task.stage == "transcribe":
        catalog_error = catalog.validate_language_support(task.stage, language)
        if catalog_error:
            # Build detailed error context (M30)
            details = await _build_error_details(
                catalog, registry, task.stage, language, word_timestamps
            )
            raise CatalogValidationError(
                catalog_error,
                stage=task.stage,
                language=language,
                details=details,
            )

    # 2. Registry check - is the engine currently running? (M28)
    if not await registry.is_engine_available(task.engine_id):
        # Build detailed error context (M30)
        details = await _build_error_details(
            catalog, registry, task.stage, language, word_timestamps
        )
        raise EngineUnavailableError(
            f"Engine '{task.engine_id}' is not available. "
            f"No healthy engine registered for stage '{task.stage}'.",
            engine_id=task.engine_id,
            stage=task.stage,
            details=details,
        )

    # 3. Capabilities check - does running engine support job requirements? (M29)
    if language and task.stage == "transcribe":
        engine_state = await registry.get_engine(task.engine_id)
        if engine_state and not engine_state.supports_language(language):
            # Build detailed error context (M30)
            details = await _build_error_details(
                catalog, registry, task.stage, language, word_timestamps
            )
            raise EngineCapabilityError(
                f"Engine '{task.engine_id}' is running but does not support "
                f"language '{language}'. Supported languages: "
                f"{engine_state.capabilities.languages if engine_state.capabilities else 'unknown'}",
                engine_id=task.engine_id,
                stage=task.stage,
                language=language,
                details=details,
            )

    task_id_str = str(task.id)
    job_id_str = str(task.job_id)

    log = logger.bind(task_id=task_id_str, job_id=job_id_str, engine_id=task.engine_id)

    # 1. Store task metadata in Redis hash (includes request_id for correlation)
    metadata_key = TASK_METADATA_KEY.format(task_id=task_id_str)
    ctx = structlog.contextvars.get_contextvars()
    metadata_mapping: dict[str, str] = {
        "job_id": job_id_str,
        "stage": task.stage,
        "engine_id": task.engine_id,
        "enqueued_at": datetime.now(
            UTC
        ).isoformat(),  # M20: For queue wait time metrics
    }
    if "request_id" in ctx:
        metadata_mapping["request_id"] = ctx["request_id"]

    # Inject trace context for distributed tracing (M19)
    trace_context = dalston.telemetry.inject_trace_context()
    if trace_context:
        metadata_mapping["_trace_context"] = json.dumps(trace_context)

    await redis.hset(
        metadata_key,
        mapping=metadata_mapping,
    )

    # Calculate TTL based on expected task duration (M30)
    # Get audio duration from metadata (prepare stage) or config
    audio_duration = None
    if audio_metadata and "duration" in audio_metadata:
        audio_duration = audio_metadata["duration"]
    elif task.config and "audio_duration" in task.config:
        audio_duration = task.config["audio_duration"]

    # Get RTF from catalog entry
    catalog_entry = catalog.get_engine(task.engine_id)
    rtf_gpu = None
    rtf_cpu = None
    if catalog_entry and catalog_entry.capabilities:
        rtf_gpu = catalog_entry.capabilities.rtf_gpu
        rtf_cpu = catalog_entry.capabilities.rtf_cpu

    # Calculate timeout with safety margin for retries (max_retries + 1 attempts)
    base_timeout = calculate_task_timeout(audio_duration, rtf_gpu, rtf_cpu)
    retry_factor = (task.max_retries + 1) if task.max_retries else 1
    metadata_ttl = base_timeout * retry_factor + 3600  # Add 1 hour buffer

    await redis.expire(metadata_key, metadata_ttl)

    log.debug(
        "stored_task_metadata",
        redis_key=metadata_key,
        ttl_seconds=metadata_ttl,
        audio_duration=audio_duration,
    )

    # 2. Write task input.json to S3
    await write_task_input(
        task=task,
        settings=settings,
        previous_outputs=previous_outputs or {},
        audio_metadata=audio_metadata,
    )

    # 3. Push task_id to engine queue
    queue_key = ENGINE_QUEUE_KEY.format(engine_id=task.engine_id)
    await redis.lpush(queue_key, task_id_str)

    log.info("task_queued", queue=queue_key)


async def remove_task_from_queue(
    redis: Redis,
    task_id: UUID,
    engine_id: str,
) -> bool:
    """Remove a task from its engine queue.

    Used during job cancellation to prevent READY tasks from being picked up.

    Args:
        redis: Async Redis client
        task_id: Task UUID to remove
        engine_id: Engine queue to remove from

    Returns:
        True if task was found and removed, False otherwise
    """
    task_id_str = str(task_id)
    queue_key = ENGINE_QUEUE_KEY.format(engine_id=engine_id)

    # LREM removes all occurrences of value from list (count=0 means all)
    removed = await redis.lrem(queue_key, 0, task_id_str)

    if removed > 0:
        logger.info(
            "task_removed_from_queue",
            task_id=task_id_str,
            engine_id=engine_id,
            queue=queue_key,
        )
        return True

    return False


async def write_task_input(
    task: Task,
    settings: Settings,
    previous_outputs: dict[str, Any],
    audio_metadata: dict[str, Any] | None = None,
) -> str:
    """Write task input.json to S3.

    Args:
        task: Task to write input for
        settings: Application settings
        previous_outputs: Outputs from dependency tasks
        audio_metadata: Audio file metadata (for prepare stage)

    Returns:
        S3 URI of the written input.json
    """
    task_id_str = str(task.id)
    job_id_str = str(task.job_id)

    # Build typed input document
    if audio_metadata:
        # Prepare stage: include full media object
        media = AudioMedia(uri=task.input_uri, **audio_metadata)
        input_data = TaskInputData(
            task_id=task_id_str,
            job_id=job_id_str,
            media=media,
            previous_outputs=previous_outputs,
            config=task.config,
        )
    else:
        # Non-prepare stages: just audio_uri
        input_data = TaskInputData(
            task_id=task_id_str,
            job_id=job_id_str,
            audio_uri=task.input_uri,
            previous_outputs=previous_outputs,
            config=task.config,
        )

    # S3 path: jobs/{job_id}/tasks/{task_id}/input.json
    s3_key = f"jobs/{job_id_str}/tasks/{task_id_str}/input.json"

    async with get_s3_client(settings) as s3:
        await s3.put_object(
            Bucket=settings.s3_bucket,
            Key=s3_key,
            Body=input_data.model_dump_json(indent=2, exclude_none=True).encode(
                "utf-8"
            ),
            ContentType="application/json",
        )

    s3_uri = f"s3://{settings.s3_bucket}/{s3_key}"

    logger.debug(
        "wrote_task_input",
        task_id=task_id_str,
        s3_uri=s3_uri,
    )

    return s3_uri


async def get_task_output(
    job_id: UUID,
    task_id: UUID,
    settings: Settings,
) -> dict[str, Any] | None:
    """Fetch task output.json from S3.

    Args:
        job_id: Job UUID
        task_id: Task UUID
        settings: Application settings

    Returns:
        Parsed output data or None if not found
    """
    s3_key = f"jobs/{job_id}/tasks/{task_id}/output.json"

    try:
        async with get_s3_client(settings) as s3:
            response = await s3.get_object(
                Bucket=settings.s3_bucket,
                Key=s3_key,
            )
            body = await response["Body"].read()
            return json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.warning(
            "task_output_not_found",
            task_id=str(task_id),
            s3_key=s3_key,
            error=str(e),
        )
        return None
