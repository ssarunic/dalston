"""Event handlers for orchestrator.

Handles Redis pub/sub events:
- job.created: Expand job into task DAG, queue first tasks
- task.completed: Advance dependent tasks, check job completion
- task.failed: Retry or fail job
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.events import publish_job_completed, publish_job_failed
from dalston.common.models import JobStatus, TaskStatus
from dalston.config import Settings
from dalston.db.models import JobModel, TaskModel
from dalston.orchestrator.dag import build_task_dag
from dalston.orchestrator.scheduler import get_task_output, queue_task

logger = structlog.get_logger()


async def handle_job_created(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
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
    """
    log = logger.bind(job_id=str(job_id))
    log.info("handling_job_created")

    # 1. Fetch job from PostgreSQL
    job = await db.get(JobModel, job_id)
    if job is None:
        log.error("job_not_found")
        return

    # 2. Build task DAG
    tasks = build_task_dag(
        job_id=job.id,
        audio_uri=job.audio_uri,
        parameters=job.parameters,
    )

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

    # 5. Queue tasks with no dependencies
    for task in tasks:
        if not task.dependencies:
            # Mark as ready
            task_model = await db.get(TaskModel, task.id)
            if task_model:
                task_model.status = TaskStatus.READY.value
                await db.commit()

            # Queue for execution
            await queue_task(
                redis=redis,
                task=task,
                settings=settings,
                previous_outputs={},
            )

            log.info("queued_initial_task", task_id=str(task.id), stage=task.stage)


async def handle_task_completed(
    task_id: UUID,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
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
    await db.commit()

    job_id = task.job_id
    log = log.bind(job_id=str(job_id), stage=task.stage)
    log.info("marked_task_completed")

    # 2. Find all tasks for this job
    result = await db.execute(select(TaskModel).where(TaskModel.job_id == job_id))
    all_tasks = list(result.scalars().all())

    # Build lookup maps
    task_by_id = {t.id: t for t in all_tasks}
    completed_ids = {t.id for t in all_tasks if t.status == TaskStatus.COMPLETED.value}

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

            # Determine input_uri (use job's audio_uri if not set)
            if not dependent.input_uri:
                job = await db.get(JobModel, job_id)
                if job:
                    dependent.input_uri = job.audio_uri
                    await db.commit()

            # Convert to Pydantic model for queue_task
            from dalston.common.models import Task

            task_model = Task.model_validate(dependent)

            await queue_task(
                redis=redis,
                task=task_model,
                settings=settings,
                previous_outputs=previous_outputs,
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

        await queue_task(
            redis=redis,
            task=task_model,
            settings=settings,
            previous_outputs=previous_outputs,
        )

        return

    # 3. Check if task is optional
    if not task.required:
        task.status = TaskStatus.SKIPPED.value
        task.error = error
        await db.commit()

        log.info("skipped_optional_task")

        # Treat as completed for dependency purposes
        await handle_task_completed(task_id, db, redis, settings)
        return

    # 4. Required task failed - fail the job
    task.status = TaskStatus.FAILED.value
    task.error = error
    await db.commit()

    job = await db.get(JobModel, job_id)
    if job:
        job.status = JobStatus.FAILED.value
        job.error = f"Task {task.stage} failed: {error}"
        job.completed_at = datetime.now(UTC)
        await db.commit()

        # Trigger webhook if configured
        if job.webhook_url:
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

    return previous_outputs


async def _check_job_completion(job_id: UUID, db: AsyncSession, redis: Redis) -> None:
    """Check if all tasks are done and mark job as completed.

    Args:
        job_id: Job UUID to check
        db: Database session
        redis: Redis client for publishing webhook events
    """
    log = logger.bind(job_id=str(job_id))

    # Expire all cached objects to ensure we read fresh data from DB
    db.expire_all()

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

    job = await db.get(JobModel, job_id)
    if job is None:
        return

    if any_failed:
        job.status = JobStatus.FAILED.value
        job.error = job.error or "One or more required tasks failed"
        log.error("job_failed", reason=job.error)
    else:
        job.status = JobStatus.COMPLETED.value
        log.info("job_completed")

    job.completed_at = datetime.now(UTC)
    await db.commit()

    # Trigger webhook if configured
    if job.webhook_url:
        if any_failed:
            await publish_job_failed(redis, job_id, job.error or "Unknown error")
        else:
            await publish_job_completed(redis, job_id)
