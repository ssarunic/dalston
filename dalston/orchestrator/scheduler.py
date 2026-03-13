"""Task scheduler for pushing ready tasks to engine queues.

Handles:
- Writing task metadata to Redis for engine lookup
- Writing task input.json to S3
- Pushing task IDs to engine-specific Redis queues
- Validating engine capabilities against job requirements (M29)
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
import structlog.contextvars
from redis.asyncio import Redis

import dalston.telemetry
from dalston.common.artifacts import ArtifactReference, ArtifactSelector, InputBinding
from dalston.common.events import publish_engine_needed
from dalston.common.models import Task
from dalston.common.pipeline_types import AudioMedia, TaskInputData
from dalston.common.registry import UnifiedEngineRegistry
from dalston.common.s3 import get_s3_client
from dalston.common.streams import add_task, add_task_once
from dalston.common.streams_types import WAITING_ENGINE_TASKS_KEY
from dalston.config import Settings
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog, get_catalog
from dalston.orchestrator.exceptions import (
    CatalogValidationError,
    EngineInfo,
    EngineUnavailableError,
    ErrorDetails,
    build_engine_suggestion,
)

logger = structlog.get_logger()

# Redis key patterns
TASK_METADATA_KEY = "dalston:task:{task_id}"
# Note: ENGINE_QUEUE_KEY removed in M33 - now using Redis Streams via dalston.common.streams

# Timeout calculation constants (M30)
MIN_TIMEOUT_S = 60  # Minimum timeout for any task
DEFAULT_RTF = 1.0  # Fallback RTF if not specified
TIMEOUT_SAFETY_FACTOR = 3.0  # Multiply estimated time by this factor


def _build_engine_info(
    entry: CatalogEntry,
    running_ids: set[str],
    unhealthy_ids: set[str],
) -> EngineInfo:
    """Build EngineInfo from catalog entry with engine_id status."""
    if entry.engine_id in running_ids:
        status = "running"
    elif entry.engine_id in unhealthy_ids:
        status = "unhealthy"
    else:
        status = "available"

    return EngineInfo(
        id=entry.engine_id,
        supports_word_timestamps=entry.capabilities.supports_word_timestamps,
        status=status,
    )


async def _build_error_details(
    catalog: EngineCatalog,
    registry: UnifiedEngineRegistry,
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
    running_engines = await registry.get_all()
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


async def _load_job_artifact_index(
    redis: Redis,
    job_id: str,
) -> dict[str, ArtifactReference]:
    """Load the job-scoped artifact index from Redis."""
    raw = await redis.hgetall(f"dalston:job:{job_id}:artifacts")
    index: dict[str, ArtifactReference] = {}
    for artifact_id, metadata_json in raw.items():
        try:
            index[artifact_id] = ArtifactReference.model_validate_json(metadata_json)
        except Exception:
            logger.warning(
                "invalid_artifact_metadata_in_index",
                job_id=job_id,
                artifact_id=artifact_id,
            )
    return index


def _matches_selector(
    ref: ArtifactReference,
    selector: ArtifactSelector,
) -> bool:
    if ref.producer_stage != selector.producer_stage:
        return False
    if ref.kind != selector.kind:
        return False
    if selector.channel is not None and ref.channel != selector.channel:
        return False
    if selector.role is not None and ref.role != selector.role:
        return False
    return True


def _resolve_input_bindings(
    *,
    bindings: list[InputBinding],
    artifact_index: dict[str, ArtifactReference],
) -> dict[str, str]:
    """Resolve slot bindings to concrete artifact IDs."""
    resolved: dict[str, str] = {}
    for binding in bindings:
        match_id = None
        for artifact_id, ref in artifact_index.items():
            if _matches_selector(ref, binding.selector):
                match_id = artifact_id
                break

        if match_id is None and binding.selector.required:
            raise ValueError(
                f"Required artifact binding missing for slot={binding.slot} "
                f"selector={binding.selector.model_dump(exclude_none=True)}"
            )
        if match_id is not None:
            resolved[binding.slot] = match_id
    return resolved


async def queue_task(
    redis: Redis,
    task: Task,
    settings: Settings,
    registry: UnifiedEngineRegistry,
    previous_outputs: dict[str, Any] | None = None,
    audio_metadata: dict[str, Any] | None = None,
    catalog: EngineCatalog | None = None,
    enqueue_idempotency_key: str | None = None,
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
        enqueue_idempotency_key: Optional idempotency key for stream enqueue.
            When provided, stream insertion is deduplicated atomically.

    Raises:
        CatalogValidationError: If no engine in catalog supports the requirements
        EngineUnavailableError: If the required engine is not running
    """
    task_id_str = str(task.id)
    job_id_str = str(task.job_id)
    queue_id = task.engine_id
    if not task.input_bindings and isinstance(task.config, dict):
        bindings_from_config = task.config.get("input_bindings")
        if isinstance(bindings_from_config, list):
            task = task.model_copy(update={"input_bindings": bindings_from_config})

    # Get language from task config (if present)
    # Normalize "auto" to None - it means auto-detect, not a language requirement
    language = task.config.get("language") if task.config else None
    if language and language.lower() == "auto":
        language = None

    # 1. Catalog check - does any engine in catalog support this?
    if catalog is None:
        catalog = get_catalog()
    catalog_entry = catalog.get_engine(task.engine_id)
    if catalog_entry is not None and catalog_entry.execution_profile != "container":
        raise CatalogValidationError(
            f"Runtime '{task.engine_id}' declares execution_profile "
            f"'{catalog_entry.execution_profile}' and cannot be queued on the "
            "distributed container path. Use the lite pipeline for inproc/venv "
            "engine_ids.",
            stage=task.stage,
        )

    # Get word_timestamps requirement from config
    word_timestamps = task.config.get("word_timestamps") if task.config else None

    # 2. Registry check - is the engine currently running? (M28)
    # Check config for behavior when engine is unavailable
    engine_unavailable_behavior = getattr(
        settings, "engine_unavailable_behavior", "fail_fast"
    )
    engine_wait_timeout_seconds = int(
        getattr(settings, "engine_wait_timeout_seconds", 300)
    )
    engine_available = await registry.is_engine_available(task.engine_id)
    waiting_for_engine = False

    if not engine_available:
        if engine_unavailable_behavior == "fail_fast":
            # Original behavior: fail immediately with detailed error
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
        else:
            # Wait mode: queue task and signal for external scaler
            waiting_for_engine = True
            logger.info(
                "engine_not_available_waiting",
                engine_id=task.engine_id,
                stage=task.stage,
                job_id=str(task.job_id),
                task_id=str(task.id),
                wait_timeout_seconds=engine_wait_timeout_seconds,
            )
            # Publish event for external scalers to start the engine
            await publish_engine_needed(
                redis,
                engine_id=task.engine_id,
                stage=task.stage,
                job_id=task.job_id,
                task_id=task.id,
                language=language,
            )

    log = logger.bind(task_id=task_id_str, job_id=job_id_str, engine_id=task.engine_id)

    # 1. Store task metadata in Redis hash (includes request_id for correlation)
    metadata_key = TASK_METADATA_KEY.format(task_id=task_id_str)
    ctx = structlog.contextvars.get_contextvars()
    metadata_mapping: dict[str, str] = {
        "job_id": job_id_str,
        "stage": task.stage,
        "engine_id": task.engine_id,
        "queue_id": queue_id,
        "execution_profile": (
            catalog_entry.execution_profile
            if catalog_entry is not None
            else "container"
        ),
        "enqueued_at": datetime.now(
            UTC
        ).isoformat(),  # M20: For queue wait time metrics
    }
    if waiting_for_engine:
        wait_enqueued_at = datetime.now(UTC)
        metadata_mapping["waiting_for_engine"] = "true"
        metadata_mapping["wait_enqueued_at"] = wait_enqueued_at.isoformat()
        metadata_mapping["wait_timeout_s"] = str(engine_wait_timeout_seconds)
        metadata_mapping["wait_deadline_at"] = (
            wait_enqueued_at + timedelta(seconds=engine_wait_timeout_seconds)
        ).isoformat()
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
    if waiting_for_engine:
        await redis.sadd(WAITING_ENGINE_TASKS_KEY, task_id_str)

    # Calculate TTL based on expected task duration (M30)
    # Get audio duration from metadata (prepare stage) or config
    audio_duration = None
    if audio_metadata and "duration" in audio_metadata:
        audio_duration = audio_metadata["duration"]
    elif task.config and "audio_duration" in task.config:
        audio_duration = task.config["audio_duration"]

    # Get RTF from catalog entry
    rtf_gpu = None
    rtf_cpu = None
    if catalog_entry and catalog_entry.capabilities:
        rtf_gpu = catalog_entry.capabilities.rtf_gpu
        rtf_cpu = catalog_entry.capabilities.rtf_cpu

    # Calculate timeout with safety margin for retries (max_retries + 1 attempts)
    base_timeout = calculate_task_timeout(audio_duration, rtf_gpu, rtf_cpu)

    # Add engine wait timeout if waiting for engine to start
    if waiting_for_engine:
        base_timeout += engine_wait_timeout_seconds
        log.debug(
            "extended_timeout_for_engine_wait",
            original_timeout=base_timeout - engine_wait_timeout_seconds,
            engine_wait_timeout=engine_wait_timeout_seconds,
            total_timeout=base_timeout,
        )

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
    input_doc = await write_task_input(
        redis=redis,
        task=task,
        settings=settings,
        previous_outputs=previous_outputs or {},
        audio_metadata=audio_metadata,
    )
    if isinstance(input_doc, dict):
        input_bindings_json = json.dumps(input_doc.get("input_bindings", []))
        resolved_artifact_ids_json = json.dumps(
            input_doc.get("resolved_artifact_ids", {})
        )
    else:
        input_bindings_json = "[]"
        resolved_artifact_ids_json = "{}"

    await redis.hset(
        metadata_key,
        mapping={
            "input_bindings_json": input_bindings_json,
            "resolved_artifact_ids_json": resolved_artifact_ids_json,
        },
    )

    # 3. Add task to stream (replaces lpush to queue)
    if enqueue_idempotency_key:
        message_id = await add_task_once(
            redis,
            stage=queue_id,
            task_id=task_id_str,
            job_id=job_id_str,
            timeout_s=base_timeout,
            dedupe_key=enqueue_idempotency_key,
        )
        if message_id is None:
            log.info(
                "task_already_enqueued",
                stream=f"dalston:stream:{queue_id}",
                dedupe_key=enqueue_idempotency_key,
            )
            return
    else:
        message_id = await add_task(
            redis,
            stage=queue_id,
            task_id=task_id_str,
            job_id=job_id_str,
            timeout_s=base_timeout,
        )

    await redis.hset(metadata_key, mapping={"stream_message_id": message_id})

    log.info("task_queued", stream=f"dalston:stream:{queue_id}", message_id=message_id)


async def write_task_input(
    redis: Redis,
    task: Task,
    settings: Settings,
    previous_outputs: dict[str, Any],
    audio_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write task input.json to S3.

    Args:
        task: Task to write input for
        settings: Application settings
        previous_outputs: Outputs from dependency tasks
        audio_metadata: Audio file metadata (for prepare stage)

    Returns:
        Dict containing S3 URI and resolved artifact metadata used in input.json
    """
    task_id_str = str(task.id)
    job_id_str = str(task.job_id)
    bindings = [InputBinding.model_validate(binding) for binding in task.input_bindings]
    artifact_index = await _load_job_artifact_index(redis, job_id_str)
    payload: dict[str, Any] | None = None

    # Prepare is the root stage: resolve original upload as an artifact reference.
    if task.stage == "prepare":
        source_artifact_id = f"{job_id_str}:source:audio"
        if not task.input_uri:
            raise ValueError("prepare task missing input_uri")
        artifact_index[source_artifact_id] = ArtifactReference(
            artifact_id=source_artifact_id,
            kind="audio",
            storage_locator=task.input_uri,
            media_type=None,
            role="source",
            producer_stage="gateway",
        )
        resolved_artifact_ids = {"audio": source_artifact_id}

        if audio_metadata:
            media = AudioMedia(artifact_id=source_artifact_id, **audio_metadata)
            payload = {"media": media.model_dump(mode="json", exclude_none=True)}
    else:
        resolved_artifact_ids = _resolve_input_bindings(
            bindings=bindings,
            artifact_index=artifact_index,
        )

    effective_config = {
        key: value for key, value in task.config.items() if key != "input_bindings"
    }

    input_data = TaskInputData(
        task_id=task_id_str,
        job_id=job_id_str,
        payload=payload,
        previous_outputs=previous_outputs,
        config=effective_config,
        input_bindings=bindings,
        resolved_artifact_ids=resolved_artifact_ids,
        artifact_index=artifact_index,
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

    return {
        "s3_uri": s3_uri,
        "input_bindings": [
            binding.model_dump(mode="json", exclude_none=True) for binding in bindings
        ],
        "resolved_artifact_ids": resolved_artifact_ids,
    }


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
