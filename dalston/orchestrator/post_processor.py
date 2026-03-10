"""Post-processing orchestration for async enrichment jobs (M67).

Manages PII detection and audio redaction as post-completion enrichments
when ``pii_mode=post_process``.  The core pipeline finishes without PII
stages, then the PostProcessor schedules them asynchronously.

Enrichment ordering:  ``pii_detect -> audio_redact``

The PostProcessor creates new tasks on the existing job, wires their
dependencies, and queues them through the standard scheduler.  Task
completion follows the same ``handle_task_completed`` path as pipeline
tasks, but the job is already in COMPLETED state so an additional
``_check_post_processing_completion`` callback finalises the enrichment
metadata on the job row.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select

from dalston.common.artifacts import ArtifactSelector, InputBinding
from dalston.common.models import Task, TaskStatus
from dalston.orchestrator.dag import DEFAULT_ENGINES, VALID_PII_REDACTION_MODES
from dalston.orchestrator.defaults import (
    DEFAULT_PII_BUFFER_MS,
    DEFAULT_PII_CONFIDENCE_THRESHOLD,
    POST_PROCESSOR_MAX_RETRIES,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from dalston.common.registry import UnifiedEngineRegistry
    from dalston.config import Settings
    from dalston.db.models import JobModel, TaskModel

logger = structlog.get_logger()


class EnrichmentStatus(StrEnum):
    """Status of a post-processing enrichment on a job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Redis key for tracking enrichment status per job
_ENRICHMENT_KEY_PREFIX = "dalston:job:{job_id}:enrichments"


def needs_post_processing(job: JobModel, pii_mode: str) -> bool:
    """Check if a completed job requires post-processing enrichments.

    Args:
        job: The completed job model.
        pii_mode: Current PII mode setting (``pipeline`` or ``post_process``).

    Returns:
        True if post-processing tasks should be scheduled.
    """
    if pii_mode != "post_process":
        return False
    params = job.parameters or {}
    return bool(params.get("pii_detection", False))


def build_post_processing_tasks(
    job: JobModel,
    *,
    stage_runtime_model_ids: dict[str, str] | None = None,
) -> list[Task]:
    """Build post-processing PII tasks for a completed job.

    Creates ``pii_detect`` (and optionally ``audio_redact``) tasks that
    depend on the already-completed pipeline tasks.

    Args:
        job: Job model (must already be COMPLETED).
        stage_runtime_model_ids: Optional runtime model overrides by stage.

    Returns:
        List of Task objects with dependencies wired to existing tasks.
    """
    params = job.parameters or {}
    stage_runtime_model_ids = stage_runtime_model_ids or {}

    tasks: list[Task] = []

    # PII detect depends on the merge task output (transcript is available)
    pii_detect_config: dict = {
        "entity_types": params.get("pii_entity_types"),
        "confidence_threshold": params.get(
            "pii_confidence_threshold", DEFAULT_PII_CONFIDENCE_THRESHOLD
        ),
        "post_processing": True,
    }
    if "pii_detect" in stage_runtime_model_ids:
        pii_detect_config["runtime_model_id"] = stage_runtime_model_ids["pii_detect"]

    pii_detect_task = Task(
        id=uuid4(),
        job_id=job.id,
        stage="pii_detect",
        runtime=DEFAULT_ENGINES["pii_detect"],
        status=TaskStatus.PENDING,
        dependencies=[],  # No DAG dependencies; job is already complete
        input_bindings=[],
        config=pii_detect_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=POST_PROCESSOR_MAX_RETRIES,
        required=False,  # Post-processing failures don't fail the job
    )
    tasks.append(pii_detect_task)

    if params.get("redact_pii_audio", False):
        redaction_mode = params.get("pii_redaction_mode", "silence")
        if redaction_mode not in VALID_PII_REDACTION_MODES:
            redaction_mode = "silence"

        audio_redact_config: dict = {
            "redaction_mode": redaction_mode,
            "buffer_ms": params.get("pii_buffer_ms", DEFAULT_PII_BUFFER_MS),
            "post_processing": True,
        }

        audio_redact_task = Task(
            id=uuid4(),
            job_id=job.id,
            stage="audio_redact",
            runtime=DEFAULT_ENGINES["audio_redact"],
            status=TaskStatus.PENDING,
            dependencies=[pii_detect_task.id],
            input_bindings=[
                InputBinding(
                    slot="audio",
                    selector=ArtifactSelector(
                        producer_stage="prepare",
                        kind="audio",
                        role="prepared",
                        required=True,
                    ),
                ).model_dump(exclude_none=True),
            ],
            config=audio_redact_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=POST_PROCESSOR_MAX_RETRIES,
            required=False,
        )
        tasks.append(audio_redact_task)

    return tasks


async def schedule_post_processing(
    job: JobModel,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    registry: UnifiedEngineRegistry,
) -> list[Task]:
    """Schedule post-processing enrichment tasks for a completed job.

    Creates tasks in the database, records enrichment tracking state in
    Redis, and queues the initial task (``pii_detect``) for execution.

    Args:
        job: Job model (status must be COMPLETED).
        db: Database session.
        redis: Redis client.
        settings: Application settings.
        registry: Engine registry for scheduler validation.

    Returns:
        List of created Task objects.
    """
    from dalston.db.models import TaskDependency, TaskModel

    log = logger.bind(job_id=str(job.id))

    tasks = build_post_processing_tasks(job)
    if not tasks:
        return []

    log.info(
        "scheduling_post_processing",
        task_count=len(tasks),
        stages=[t.stage for t in tasks],
    )

    # Persist tasks
    for task in tasks:
        task_config = dict(task.config)
        if task.input_bindings:
            task_config["input_bindings"] = task.input_bindings
        task_model = TaskModel(
            id=task.id,
            job_id=task.job_id,
            stage=task.stage,
            runtime=task.runtime,
            status=task.status.value,
            config=task_config,
            input_uri=task.input_uri,
            output_uri=task.output_uri,
            retries=task.retries,
            max_retries=task.max_retries,
            required=task.required,
        )
        db.add(task_model)

    await db.flush()

    for task in tasks:
        for dep_id in task.dependencies:
            db.add(TaskDependency(task_id=task.id, depends_on_id=dep_id))

    await db.commit()

    # Record enrichment tracking in Redis
    enrichment_key = _ENRICHMENT_KEY_PREFIX.format(job_id=str(job.id))
    enrichment_data = {t.stage: EnrichmentStatus.PENDING.value for t in tasks}
    await redis.hset(enrichment_key, mapping=enrichment_data)
    await redis.expire(enrichment_key, 86400)  # 24h TTL

    # Queue the first task (pii_detect has no dependencies)
    from dalston.orchestrator.scheduler import queue_task

    first_task = tasks[0]
    first_task_model = await db.get(TaskModel, first_task.id)
    if first_task_model:
        first_task_model.status = TaskStatus.READY.value
        await db.commit()

    # Gather previous outputs from completed pipeline tasks to feed into
    # the post-processing pii_detect task.  We look for outputs from
    # transcribe, align, and diarize (same inputs the pipeline-mode
    # pii_detect would receive).
    from dalston.orchestrator.handlers import _gather_previous_outputs

    all_tasks_result = await db.execute(
        select(TaskModel).where(TaskModel.job_id == job.id)
    )
    all_tasks_models = list(all_tasks_result.scalars().all())
    task_by_id = {t.id: t for t in all_tasks_models}

    # Find completed pipeline tasks that would feed PII detect
    pipeline_stages = {"transcribe", "align", "diarize"}
    pipeline_dep_ids = [
        t.id
        for t in all_tasks_models
        if t.stage in pipeline_stages and t.status == TaskStatus.COMPLETED.value
    ]

    previous_outputs = await _gather_previous_outputs(
        dependency_ids=pipeline_dep_ids,
        task_by_id=task_by_id,
        settings=settings,
    )

    try:
        await queue_task(
            redis=redis,
            task=first_task,
            settings=settings,
            registry=registry,
            previous_outputs=previous_outputs,
        )
    except Exception as e:
        log.error("post_processing_queue_failed", error=str(e))
        # Mark enrichment as failed in Redis but don't fail the job
        await redis.hset(
            enrichment_key, first_task.stage, EnrichmentStatus.FAILED.value
        )
        raise

    log.info("post_processing_scheduled", first_stage=first_task.stage)
    return tasks


async def check_post_processing_completion(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> bool:
    """Check if all post-processing enrichments for a job are done.

    Called after a post-processing task completes to see if all enrichments
    have finished.

    Args:
        job_id: Job UUID.
        db: Database session.
        redis: Redis client.

    Returns:
        True if all enrichments are complete (or none were scheduled).
    """
    enrichment_key = _ENRICHMENT_KEY_PREFIX.format(job_id=str(job_id))
    enrichments = await redis.hgetall(enrichment_key)

    if not enrichments:
        return True

    all_done = all(
        v.decode()
        if isinstance(v, bytes)
        else v in (EnrichmentStatus.COMPLETED.value, EnrichmentStatus.FAILED.value)
        for v in enrichments.values()
    )

    return all_done


async def mark_enrichment_status(
    job_id: UUID,
    stage: str,
    status: EnrichmentStatus,
    redis: Redis,
) -> None:
    """Update the enrichment status for a specific stage.

    Args:
        job_id: Job UUID.
        stage: Enrichment stage name (e.g. ``pii_detect``).
        status: New status.
        redis: Redis client.
    """
    enrichment_key = _ENRICHMENT_KEY_PREFIX.format(job_id=str(job_id))
    await redis.hset(enrichment_key, stage, status.value)


def is_post_processing_task(task: TaskModel) -> bool:
    """Check if a task is a post-processing enrichment task.

    Post-processing tasks have ``post_processing: True`` in their config.
    """
    config = task.config if isinstance(task.config, dict) else {}
    return bool(config.get("post_processing", False))
