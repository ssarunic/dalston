"""Event handlers for orchestrator.

Handles Redis pub/sub events:
- job.created: Expand job into task DAG, queue first tasks
- task.completed: Advance dependent tasks, check job completion
- task.failed: Retry or fail job
"""

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import dalston.metrics
from dalston.common.events import (
    publish_job_cancelled,
    publish_job_completed,
    publish_job_failed,
)
from dalston.common.models import JobStatus, RetentionMode, TaskStatus
from dalston.common.s3 import get_s3_client
from dalston.common.streams import mark_job_cancelled
from dalston.config import Settings, get_settings
from dalston.db.models import JobModel, TaskModel
from dalston.gateway.services.rate_limiter import (
    CONCURRENT_COUNTER_TTL_SECONDS,
    KEY_PREFIX_JOB_DECREMENTED,
    KEY_PREFIX_JOBS,
)
from dalston.orchestrator.catalog import get_catalog
from dalston.orchestrator.dag import build_task_dag, build_task_dag_async
from dalston.orchestrator.engine_selector import NoCapableEngineError
from dalston.orchestrator.exceptions import (
    CatalogValidationError,
    EngineCapabilityError,
    EngineUnavailableError,
)
from dalston.orchestrator.registry import BatchEngineRegistry
from dalston.orchestrator.scheduler import (
    get_task_output,
    queue_task,
)
from dalston.orchestrator.stats import extract_stats_from_transcript

logger = structlog.get_logger()


def _serialize_engine_error(
    e: EngineUnavailableError | EngineCapabilityError | CatalogValidationError,
) -> str:
    """Serialize engine error with details to JSON string (M30).

    If the exception has a to_dict method (enhanced exceptions), serialize
    the full error dict. Otherwise, fall back to str(e).

    Args:
        e: The exception to serialize

    Returns:
        JSON string if exception has details, otherwise str(e)
    """
    if hasattr(e, "to_dict"):
        return json.dumps(e.to_dict())
    return str(e)


async def _decrement_concurrent_jobs(
    redis: Redis, job_id: UUID, tenant_id: UUID
) -> bool:
    """Idempotent decrement of concurrent job count for rate limiting.

    Called when a job transitions to a terminal state (completed, failed, cancelled).
    Uses SET NX guard to ensure the counter is decremented exactly once per job,
    preventing both leaks and double-decrements.

    Args:
        redis: Redis client
        job_id: Job UUID used as idempotency key
        tenant_id: Tenant UUID for the counter key

    Returns:
        True if this call performed the decrement, False if already decremented.
    """
    guard_key = f"{KEY_PREFIX_JOB_DECREMENTED}:{job_id}"

    # SET NX returns True only if key was set (didn't exist)
    was_set = await redis.set(
        guard_key, "1", nx=True, ex=CONCURRENT_COUNTER_TTL_SECONDS
    )

    if not was_set:
        logger.debug(
            "decrement_already_done",
            job_id=str(job_id),
            tenant_id=str(tenant_id),
        )
        return False

    # Guard was set - we own the decrement
    counter_key = f"{KEY_PREFIX_JOBS}:{tenant_id}"
    result = await redis.decr(counter_key)
    if result < 0:
        await redis.set(counter_key, 0)

    logger.debug(
        "decremented_concurrent_jobs",
        job_id=str(job_id),
        tenant_id=str(tenant_id),
    )
    return True


async def handle_job_created(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    registry: BatchEngineRegistry,
) -> None:
    """Handle job.created event.

    Steps:
    1. Fetch job from PostgreSQL
    2. Build task DAG
    3. Save all tasks to PostgreSQL
    4. Update job status to 'running'
    5. Queue tasks with no dependencies

    Args:
        job_id: UUID of the created job
        db: Database session
        redis: Redis client
        settings: Application settings
        registry: Batch engine registry for availability checks
    """
    log = logger.bind(job_id=str(job_id))
    log.info("handling_job_created")

    # 1. Fetch job from PostgreSQL
    job = await db.get(JobModel, job_id)
    if job is None:
        log.error("job_not_found")
        return

    # Check if job was cancelled before we could start
    if job.status in (JobStatus.CANCELLING.value, JobStatus.CANCELLED.value):
        log.info("job_already_cancelled_skipping_dag_build")
        return

    # Idempotency check: skip if job already running (another orchestrator got it first)
    if job.status == JobStatus.RUNNING.value:
        log.info("job_already_running_skipping_dag_build")
        return

    # Double-check: verify no tasks exist yet (handles race condition)
    existing_tasks = await db.execute(
        select(TaskModel.id).where(TaskModel.job_id == job_id).limit(1)
    )
    if existing_tasks.scalar_one_or_none() is not None:
        log.info("tasks_already_exist_skipping_dag_build")
        return

    # 2. Build task DAG using capability-driven engine selection (M31)
    dag_start = time.perf_counter()
    try:
        catalog = get_catalog()
        tasks = await build_task_dag_async(
            job_id=job.id,
            audio_uri=job.audio_uri,
            parameters=job.parameters,
            registry=registry,
            catalog=catalog,
        )
    except NoCapableEngineError as e:
        # No running engine can handle the job requirements
        # Fall back to legacy DAG builder (uses hardcoded defaults)
        log.warning(
            "capability_driven_dag_failed_using_fallback",
            stage=e.stage,
            requirements=e.requirements,
        )
        tasks = build_task_dag(
            job_id=job.id,
            audio_uri=job.audio_uri,
            parameters=job.parameters,
        )
    dalston.metrics.observe_orchestrator_dag_build(time.perf_counter() - dag_start)

    log.info("built_task_dag", task_count=len(tasks))

    # 3. Save all tasks to PostgreSQL
    for task in tasks:
        log.info(
            "creating_task",
            task_id=str(task.id),
            stage=task.stage,
            engine_id=task.engine_id,
        )
        task_model = TaskModel(
            id=task.id,
            job_id=task.job_id,
            stage=task.stage,
            engine_id=task.engine_id,
            status=task.status.value,
            dependencies=list(task.dependencies),
            config=task.config,
            input_uri=task.input_uri,
            output_uri=task.output_uri,
            retries=task.retries,
            max_retries=task.max_retries,
            required=task.required,
        )
        db.add(task_model)

    # 4. Update job status to 'running'
    job.status = JobStatus.RUNNING.value
    job.started_at = datetime.now(UTC)

    await db.commit()

    log.info("saved_tasks_and_updated_job", status="running")

    # Build audio metadata from job (already probed at upload)
    audio_metadata: dict[str, Any] | None = None
    if job.audio_format is not None:
        audio_metadata = {
            "format": job.audio_format,
            "duration": job.audio_duration,
            "sample_rate": job.audio_sample_rate,
            "channels": job.audio_channels,
            "bit_depth": job.audio_bit_depth,
        }

    # 5. Queue tasks with no dependencies
    for task in tasks:
        if not task.dependencies:
            # Mark as ready
            task_model = await db.get(TaskModel, task.id)
            if task_model:
                task_model.status = TaskStatus.READY.value
                await db.commit()

            # Queue for execution (include audio metadata for prepare stage)
            try:
                await queue_task(
                    redis=redis,
                    task=task,
                    settings=settings,
                    registry=registry,
                    previous_outputs={},
                    audio_metadata=audio_metadata if task.stage == "prepare" else None,
                )
            except (
                EngineUnavailableError,
                EngineCapabilityError,
                CatalogValidationError,
            ) as e:
                # Fail the job immediately if engine is unavailable or incapable
                # Serialize with full details (M30)
                error_str = _serialize_engine_error(e)
                job.status = JobStatus.FAILED.value
                job.error = error_str
                job.completed_at = datetime.now(UTC)
                await db.commit()
                await _decrement_concurrent_jobs(redis, job_id, job.tenant_id)
                await publish_job_failed(redis, job_id, error_str)
                log.error(
                    "job_failed_engine_error",
                    error_type=type(e).__name__,
                    engine_id=getattr(e, "engine_id", None),
                    stage=getattr(e, "stage", None),
                )
                return

            # Record task scheduled metric (M20)
            dalston.metrics.inc_orchestrator_tasks_scheduled(task.engine_id, task.stage)

            log.info("queued_initial_task", task_id=str(task.id), stage=task.stage)


async def handle_task_started(
    task_id: UUID,
    db: AsyncSession,
    engine_id: str | None = None,
) -> None:
    """Handle task.started event with atomic claim.

    Uses atomic UPDATE with WHERE clause to prevent race conditions:
    - Only transitions from READY to RUNNING
    - If task is already RUNNING (idempotent replay), logs and returns
    - If task is in another state (e.g., CANCELLED), rejects the claim

    This ensures that with Redis Streams recovery (XCLAIM), only one
    engine's claim is recorded even if multiple task.started events
    are received.

    Args:
        task_id: UUID of the started task
        db: Database session
        engine_id: ID of the engine claiming the task (for logging)
    """
    from sqlalchemy import update

    log = logger.bind(task_id=str(task_id), engine_id=engine_id)
    log.info("handling_task_started")

    # Atomic UPDATE: only transition READY -> RUNNING
    # This prevents race conditions when multiple engines try to claim
    result = await db.execute(
        update(TaskModel)
        .where(TaskModel.id == task_id)
        .where(TaskModel.status == TaskStatus.READY.value)
        .values(
            status=TaskStatus.RUNNING.value,
            started_at=datetime.now(UTC),
        )
        .returning(TaskModel.stage)
    )
    updated = result.scalar_one_or_none()

    if updated:
        await db.commit()
        log.info("marked_task_running", stage=updated)
        return

    # Atomic update failed - check why
    task = await db.get(TaskModel, task_id)
    if task is None:
        log.error("task_not_found")
        return

    if task.status == TaskStatus.RUNNING.value:
        # Already running - this is idempotent (e.g., duplicate event or recovery)
        log.debug("task_already_running", stage=task.stage)
        return

    # Task is in an unexpected state (e.g., CANCELLED, COMPLETED)
    log.warning(
        "task_claim_rejected",
        stage=task.stage,
        current_status=task.status,
        reason="task_not_in_ready_state",
    )


async def handle_task_completed(
    task_id: UUID,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    registry: BatchEngineRegistry,
) -> None:
    """Handle task.completed event.

    Steps:
    1. Mark task as completed
    2. Find dependent tasks
    3. For each dependent, check if all deps are completed
    4. Queue ready dependents
    5. If all tasks completed, mark job as completed

    Args:
        task_id: UUID of the completed task
        db: Database session
        redis: Redis client
        settings: Application settings
        registry: Batch engine registry for availability checks
    """
    log = logger.bind(task_id=str(task_id))
    log.info("handling_task_completed")

    # 1. Mark task as completed
    task = await db.get(TaskModel, task_id)
    if task is None:
        log.error("task_not_found")
        return

    task.status = TaskStatus.COMPLETED.value
    task.completed_at = datetime.now(UTC)
    task.error = None  # Clear any error from a previous failed attempt
    await db.commit()

    job_id = task.job_id
    log = log.bind(job_id=str(job_id), stage=task.stage)
    log.info("marked_task_completed")

    # Record task completion metric (M20)
    dalston.metrics.inc_orchestrator_tasks_completed(task.engine_id, "success")

    # Update job audio_duration from prepare stage output if not already set
    # This handles enhancement jobs that don't have duration set at creation time
    if task.stage == "prepare":
        job = await db.get(JobModel, job_id)
        if job and job.audio_duration is None:
            output = await get_task_output(
                job_id=job_id,
                task_id=task_id,
                settings=settings,
            )
            if output and "data" in output:
                prepare_data = output["data"]
                # The prepare stage outputs channel_files with duration
                channel_files = prepare_data.get("channel_files", [])
                if channel_files and "duration" in channel_files[0]:
                    job.audio_duration = channel_files[0]["duration"]
                    await db.commit()
                    log.info(
                        "updated_job_audio_duration",
                        audio_duration=job.audio_duration,
                    )

    # 2. Find all tasks for this job
    result = await db.execute(select(TaskModel).where(TaskModel.job_id == job_id))
    all_tasks = list(result.scalars().all())

    # Build lookup maps
    task_by_id = {t.id: t for t in all_tasks}
    completed_ids = {t.id for t in all_tasks if t.status == TaskStatus.COMPLETED.value}

    # Check if job is being cancelled - don't queue new tasks
    job = await db.get(JobModel, job_id)
    if job and job.status == JobStatus.CANCELLING.value:
        log.info("job_cancelling_skipping_dependent_tasks")
        # Mark all pending dependents as cancelled
        for dependent in all_tasks:
            if dependent.status == TaskStatus.PENDING.value:
                dependent.status = TaskStatus.CANCELLED.value
        await db.commit()
        # Check if cancellation is complete
        await _check_job_cancellation_complete(job_id, db, redis)
        return

    # 3. Find dependent tasks and check if they're ready
    for dependent in all_tasks:
        if dependent.status != TaskStatus.PENDING.value:
            continue

        # Check if all dependencies are completed
        deps_met = all(dep_id in completed_ids for dep_id in dependent.dependencies)

        if deps_met:
            # 4. Queue this dependent task
            dependent.status = TaskStatus.READY.value
            await db.commit()

            # Gather outputs from dependencies
            previous_outputs = await _gather_previous_outputs(
                dependency_ids=dependent.dependencies,
                task_by_id=task_by_id,
                settings=settings,
            )

            # Determine input_uri from prepare output
            if not dependent.input_uri:
                audio_uri = _resolve_audio_uri_from_prepare(
                    dependent.stage, previous_outputs
                )
                if audio_uri:
                    dependent.input_uri = audio_uri
                else:
                    # Fallback to original audio (shouldn't happen normally)
                    job = await db.get(JobModel, job_id)
                    if job:
                        dependent.input_uri = job.audio_uri
                await db.commit()

            # Convert to Pydantic model for queue_task
            from dalston.common.models import Task

            task_model = Task.model_validate(dependent)

            try:
                await queue_task(
                    redis=redis,
                    task=task_model,
                    settings=settings,
                    registry=registry,
                    previous_outputs=previous_outputs,
                )
            except (
                EngineUnavailableError,
                EngineCapabilityError,
                CatalogValidationError,
            ) as e:
                # Fail the job immediately if engine is unavailable or incapable
                # Serialize with full details (M30)
                error_str = _serialize_engine_error(e)
                job = await db.get(JobModel, job_id)
                if job:
                    job.status = JobStatus.FAILED.value
                    job.error = error_str
                    job.completed_at = datetime.now(UTC)
                    await db.commit()
                    await _decrement_concurrent_jobs(redis, job_id, job.tenant_id)
                    await publish_job_failed(redis, job_id, error_str)
                log.error(
                    "job_failed_engine_error",
                    error_type=type(e).__name__,
                    engine_id=getattr(e, "engine_id", None),
                    stage=getattr(e, "stage", None),
                )
                return

            # Record task scheduled metric (M20)
            dalston.metrics.inc_orchestrator_tasks_scheduled(
                dependent.engine_id, dependent.stage
            )

            log.info(
                "queued_dependent_task",
                dependent_task_id=str(dependent.id),
                dependent_stage=dependent.stage,
            )

    # 5. Check if job is complete
    await _check_job_completion(job_id, db, redis)


async def handle_task_failed(
    task_id: UUID,
    error: str,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    registry: BatchEngineRegistry,
) -> None:
    """Handle task.failed event.

    Steps:
    1. Fetch task
    2. If retries < max_retries: increment retries, re-queue
    3. Else if not required: mark as skipped, treat as completed
    4. Else: mark job as failed

    Args:
        task_id: UUID of the failed task
        error: Error message
        db: Database session
        redis: Redis client
        settings: Application settings
        registry: Batch engine registry for availability checks
    """
    log = logger.bind(task_id=str(task_id))
    log.info("handling_task_failed", error=error)

    # 1. Fetch task
    task = await db.get(TaskModel, task_id)
    if task is None:
        log.error("task_not_found")
        return

    job_id = task.job_id
    log = log.bind(job_id=str(job_id), stage=task.stage)

    # 2. Check if we can retry
    if task.retries < task.max_retries:
        task.retries += 1
        task.status = TaskStatus.READY.value
        task.error = error
        await db.commit()

        log.info(
            "retrying_task", retry_count=task.retries, max_retries=task.max_retries
        )

        # Re-queue the task
        from dalston.common.models import Task

        task_model = Task.model_validate(task)

        # Get previous outputs for retry
        result = await db.execute(select(TaskModel).where(TaskModel.job_id == job_id))
        all_tasks = list(result.scalars().all())
        task_by_id = {t.id: t for t in all_tasks}

        previous_outputs = await _gather_previous_outputs(
            dependency_ids=task.dependencies,
            task_by_id=task_by_id,
            settings=settings,
        )

        try:
            await queue_task(
                redis=redis,
                task=task_model,
                settings=settings,
                registry=registry,
                previous_outputs=previous_outputs,
            )
        except (
            EngineUnavailableError,
            EngineCapabilityError,
            CatalogValidationError,
        ) as e:
            # Engine became unavailable or incapable during retry - fail the job
            # Serialize with full details (M30)
            error_str = _serialize_engine_error(e)
            task.status = TaskStatus.FAILED.value
            task.error = error_str
            await db.commit()
            job = await db.get(JobModel, job_id)
            if job:
                job.status = JobStatus.FAILED.value
                job.error = error_str
                job.completed_at = datetime.now(UTC)
                await db.commit()
                await _decrement_concurrent_jobs(redis, job_id, job.tenant_id)
                await publish_job_failed(redis, job_id, error_str)
            log.error(
                "job_failed_engine_error_on_retry",
                error_type=type(e).__name__,
                engine_id=getattr(e, "engine_id", None),
                stage=getattr(e, "stage", None),
            )
            return

        return

    # 3. Check if task is optional
    if not task.required:
        task.status = TaskStatus.SKIPPED.value
        task.error = error
        await db.commit()

        log.info("skipped_optional_task")

        # Treat as completed for dependency purposes
        await handle_task_completed(task_id, db, redis, settings, registry)
        return

    # 4. Required task failed - fail the job
    task.status = TaskStatus.FAILED.value
    task.error = error
    await db.commit()

    # Record task failure metric (M20)
    dalston.metrics.inc_orchestrator_tasks_completed(task.engine_id, "failure")

    job = await db.get(JobModel, job_id)
    if job:
        job.status = JobStatus.FAILED.value
        job.error = f"Task {task.stage} failed: {error}"
        job.completed_at = datetime.now(UTC)
        await db.commit()

        # Decrement concurrent job count for rate limiting
        await _decrement_concurrent_jobs(redis, job_id, job.tenant_id)

        # Publish job failed event for webhook delivery
        await publish_job_failed(redis, job_id, job.error)

    log.error("job_failed", reason=f"Task {task.stage} failed: {error}")


async def _gather_previous_outputs(
    dependency_ids: list[UUID],
    task_by_id: dict[UUID, TaskModel],
    settings: Settings,
) -> dict[str, Any]:
    """Gather outputs from completed dependency tasks.

    Args:
        dependency_ids: List of dependency task UUIDs
        task_by_id: Map of task ID to TaskModel
        settings: Application settings

    Returns:
        Dict mapping stage name to output data
    """
    previous_outputs: dict[str, Any] = {}

    for dep_id in dependency_ids:
        dep_task = task_by_id.get(dep_id)
        if dep_task is None:
            continue

        output = await get_task_output(
            job_id=dep_task.job_id,
            task_id=dep_task.id,
            settings=settings,
        )

        if output and "data" in output:
            previous_outputs[dep_task.stage] = output["data"]

            # For per-channel stages (e.g. transcribe_ch0), also add the
            # base stage key (e.g. "transcribe") so downstream engines that
            # look up previous_outputs["transcribe"] can find the data.
            base_stage = re.sub(r"_ch\d+$", "", dep_task.stage)
            if base_stage != dep_task.stage:
                previous_outputs[base_stage] = output["data"]

    return previous_outputs


async def _compute_purge_after(job: JobModel, log) -> None:
    """Compute purge_after based on job's retention settings.

    Args:
        job: Job model with retention settings
        log: Logger instance
    """
    if job.retention_mode == RetentionMode.AUTO_DELETE.value:
        if job.retention_hours and job.completed_at:
            job.purge_after = job.completed_at + timedelta(hours=job.retention_hours)
            log.info(
                "purge_scheduled",
                purge_after=job.purge_after.isoformat(),
                retention_hours=job.retention_hours,
            )
    elif job.retention_mode == RetentionMode.NONE.value:
        # Immediate purge - set purge_after to now, cleanup worker will process
        job.purge_after = datetime.now(UTC)
        log.info("immediate_purge_scheduled", retention_mode="none")
    # mode == "keep": purge_after stays NULL (never purge)


async def _populate_job_result_stats(job: JobModel, log) -> None:
    """Fetch transcript and populate result stats on job.

    Called when a job successfully completes. Reads the final transcript
    from S3 and extracts summary statistics.

    Args:
        job: Job model to update with stats
        log: Logger instance
    """
    settings = get_settings()
    transcript_uri = f"s3://{settings.s3_bucket}/jobs/{job.id}/transcript.json"

    try:
        # Parse S3 URI
        uri_parts = transcript_uri.replace("s3://", "").split("/", 1)
        bucket = uri_parts[0]
        key = uri_parts[1] if len(uri_parts) > 1 else ""

        async with get_s3_client(settings) as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            body = await response["Body"].read()
            transcript = json.loads(body.decode("utf-8"))

        # Extract stats from transcript
        stats = extract_stats_from_transcript(transcript)

        # Update job model
        job.result_language_code = stats.language_code
        job.result_word_count = stats.word_count
        job.result_segment_count = stats.segment_count
        job.result_speaker_count = stats.speaker_count
        job.result_character_count = stats.character_count

        log.info(
            "job_result_stats_populated",
            language_code=stats.language_code,
            word_count=stats.word_count,
            segment_count=stats.segment_count,
            speaker_count=stats.speaker_count,
            character_count=stats.character_count,
        )

    except Exception as e:
        # Don't fail the job if stats extraction fails - just log and continue
        log.warning(
            "job_result_stats_extraction_failed",
            transcript_uri=transcript_uri,
            error=str(e),
        )


async def _check_job_completion(job_id: UUID, db: AsyncSession, redis: Redis) -> None:
    """Check if all tasks are done and mark job as completed.

    Args:
        job_id: Job UUID to check
        db: Database session
        redis: Redis client for publishing webhook events
    """
    log = logger.bind(job_id=str(job_id))

    # Fetch fresh data from DB (explicit SELECT avoids stale identity map)
    result = await db.execute(select(TaskModel).where(TaskModel.job_id == job_id))
    all_tasks = list(result.scalars().all())

    # Check if all tasks are in a terminal state
    terminal_states = {
        TaskStatus.COMPLETED.value,
        TaskStatus.SKIPPED.value,
        TaskStatus.FAILED.value,
    }

    all_done = all(t.status in terminal_states for t in all_tasks)

    if not all_done:
        pending_stages = [t.stage for t in all_tasks if t.status not in terminal_states]
        log.debug("job_not_complete_yet", pending_stages=pending_stages)
        return

    # Check if any required task failed
    any_failed = any(
        t.status == TaskStatus.FAILED.value and t.required for t in all_tasks
    )

    job_result = await db.execute(select(JobModel).where(JobModel.id == job_id))
    job = job_result.scalar_one_or_none()
    if job is None:
        return

    if any_failed:
        job.status = JobStatus.FAILED.value
        job.error = job.error or "One or more required tasks failed"
        dalston.metrics.inc_orchestrator_jobs("failed")
        log.error("job_failed", reason=job.error)
    else:
        job.status = JobStatus.COMPLETED.value
        dalston.metrics.inc_orchestrator_jobs("completed")
        # Record job duration (M20)
        if job.started_at:
            duration = (datetime.now(UTC) - job.started_at).total_seconds()
            dalston.metrics.observe_orchestrator_job_duration(len(all_tasks), duration)
        log.info("job_completed")

        # Extract and store result stats from transcript
        await _populate_job_result_stats(job, log)

    job.completed_at = datetime.now(UTC)

    # Compute purge_after based on retention settings (M25)
    await _compute_purge_after(job, log)

    await db.commit()

    # Decrement concurrent job count for rate limiting
    await _decrement_concurrent_jobs(redis, job_id, job.tenant_id)

    # Publish job completion event for webhook delivery
    # This triggers both admin-registered webhooks and per-job webhook_url (legacy)
    if any_failed:
        await publish_job_failed(redis, job_id, job.error or "Unknown error")
    else:
        await publish_job_completed(redis, job_id)


def _resolve_audio_uri_from_prepare(
    stage: str, previous_outputs: dict[str, Any]
) -> str | None:
    """Resolve audio URI from prepare output.

    For per-channel stages (transcribe_ch0, align_ch0), returns the matching
    channel's audio URI. For regular stages, returns channel_files[0].

    Returns:
        The audio URI from prepare output, or None if not available.
    """
    prepare_output = previous_outputs.get("prepare", {})
    channel_files = prepare_output.get("channel_files", [])

    if not channel_files:
        return None

    # Check for per-channel stage (e.g., transcribe_ch0, align_ch1)
    match = re.match(r"(?:transcribe|align)_ch(\d+)", stage)
    if match:
        channel = int(match.group(1))
        if channel < len(channel_files):
            return channel_files[channel].get("uri")
        return None

    # Regular stage - use first (and only) channel file
    return channel_files[0].get("uri")


async def handle_job_cancel_requested(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> None:
    """Handle job.cancel_requested event.

    Steps:
    1. Mark job as cancelled in Redis (for engines to check)
    2. Fetch all tasks for the job
    3. For each PENDING/READY task: mark as CANCELLED
    4. Check if cancellation is complete

    With Redis Streams (M33), we can't remove tasks from the stream.
    Instead, engines check the cancellation flag before processing.

    Args:
        job_id: UUID of the job to cancel
        db: Database session
        redis: Redis client
    """
    log = logger.bind(job_id=str(job_id))
    log.info("handling_job_cancel_requested")

    # 1. Mark job as cancelled in Redis so engines can skip tasks
    await mark_job_cancelled(redis, str(job_id))

    # 2. Fetch all tasks for this job
    result = await db.execute(select(TaskModel).where(TaskModel.job_id == job_id))
    all_tasks = list(result.scalars().all())

    cancelled_count = 0

    for task in all_tasks:
        # Mark PENDING and READY tasks as cancelled
        # READY tasks in the stream will be skipped by engines (via cancellation check)
        if task.status in (TaskStatus.PENDING.value, TaskStatus.READY.value):
            task.status = TaskStatus.CANCELLED.value
            cancelled_count += 1

    await db.commit()

    log.info("cancelled_pending_ready_tasks", cancelled_count=cancelled_count)

    # 3. Check if cancellation is complete
    await _check_job_cancellation_complete(job_id, db, redis)


async def _check_job_cancellation_complete(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> None:
    """Check if job cancellation is complete and finalize if so.

    A job cancellation is complete when:
    - Job status is CANCELLING
    - No tasks are RUNNING

    Args:
        job_id: Job UUID to check
        db: Database session
        redis: Redis client for publishing webhook events
    """
    log = logger.bind(job_id=str(job_id))

    # Fetch fresh data from DB (explicit SELECT avoids stale identity map)
    job_result = await db.execute(select(JobModel).where(JobModel.id == job_id))
    job = job_result.scalar_one_or_none()
    if job is None:
        return

    # Only process if job is in CANCELLING state
    if job.status != JobStatus.CANCELLING.value:
        return

    # Check if any tasks are still running
    result = await db.execute(select(TaskModel).where(TaskModel.job_id == job_id))
    all_tasks = list(result.scalars().all())

    running_tasks = [t for t in all_tasks if t.status == TaskStatus.RUNNING.value]

    if running_tasks:
        log.debug(
            "cancellation_waiting_for_tasks",
            running_count=len(running_tasks),
            running_stages=[t.stage for t in running_tasks],
        )
        return

    # All tasks are terminal - finalize cancellation
    job.status = JobStatus.CANCELLED.value
    job.completed_at = datetime.now(UTC)
    await db.commit()

    # Decrement concurrent job count for rate limiting
    await _decrement_concurrent_jobs(redis, job_id, job.tenant_id)

    # Record job cancellation metric (M20)
    dalston.metrics.inc_orchestrator_jobs("cancelled")

    log.info("job_cancelled")

    # Publish job.cancelled event for webhook delivery
    await publish_job_cancelled(redis, job_id)
