"""Task scheduler for pushing ready tasks to engine queues.

Handles:
- Writing task metadata to Redis for engine lookup
- Writing task input.json to S3
- Pushing task IDs to engine-specific Redis queues
"""

import json
from typing import Any
from uuid import UUID

import structlog
import structlog.contextvars
from redis.asyncio import Redis

from dalston.common.models import Task
from dalston.common.pipeline_types import AudioMedia, TaskInputData
from dalston.common.s3 import get_s3_client
from dalston.config import Settings

logger = structlog.get_logger()

# Redis key patterns
TASK_METADATA_KEY = "dalston:task:{task_id}"
ENGINE_QUEUE_KEY = "dalston:queue:{engine_id}"


async def queue_task(
    redis: Redis,
    task: Task,
    settings: Settings,
    previous_outputs: dict[str, Any] | None = None,
    audio_metadata: dict[str, Any] | None = None,
) -> None:
    """Queue a task for execution by its engine.

    Steps:
    1. Store task metadata in Redis hash (for engine lookup)
    2. Write task input.json to S3
    3. Push task_id to engine queue

    Args:
        redis: Async Redis client
        task: Task to queue
        settings: Application settings (for S3 bucket)
        previous_outputs: Outputs from dependency tasks (keyed by stage)
        audio_metadata: Audio file metadata (format, duration, sample_rate, channels)
    """
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
    }
    if "request_id" in ctx:
        metadata_mapping["request_id"] = ctx["request_id"]
    await redis.hset(
        metadata_key,
        mapping=metadata_mapping,
    )
    # Set TTL of 24 hours (tasks should complete much faster)
    await redis.expire(metadata_key, 86400)

    log.debug("stored_task_metadata", redis_key=metadata_key)

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
