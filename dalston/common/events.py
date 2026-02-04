"""Redis pub/sub event publishing."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

# Event channel for orchestrator communication
EVENTS_CHANNEL = "dalston:events"


def _json_serializer(obj: Any) -> str:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def publish_event(
    redis: Redis,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Publish an event to the dalston:events channel.

    Args:
        redis: Async Redis client
        event_type: Event type (e.g., "job.created", "task.completed")
        payload: Event payload (will have type and timestamp added)
    """
    event = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }
    message = json.dumps(event, default=_json_serializer)
    await redis.publish(EVENTS_CHANNEL, message)


async def publish_job_created(redis: Redis, job_id: UUID) -> None:
    """Publish a job.created event."""
    await publish_event(redis, "job.created", {"job_id": job_id})


async def publish_task_completed(
    redis: Redis,
    task_id: UUID,
    job_id: UUID,
    stage: str,
) -> None:
    """Publish a task.completed event."""
    await publish_event(
        redis,
        "task.completed",
        {"task_id": task_id, "job_id": job_id, "stage": stage},
    )


async def publish_task_failed(
    redis: Redis,
    task_id: UUID,
    job_id: UUID,
    error: str,
) -> None:
    """Publish a task.failed event."""
    await publish_event(
        redis,
        "task.failed",
        {"task_id": task_id, "job_id": job_id, "error": error},
    )


async def publish_job_completed(redis: Redis, job_id: UUID) -> None:
    """Publish a job.completed event for webhook delivery."""
    await publish_event(redis, "job.completed", {"job_id": job_id})


async def publish_job_failed(redis: Redis, job_id: UUID, error: str) -> None:
    """Publish a job.failed event for webhook delivery."""
    await publish_event(redis, "job.failed", {"job_id": job_id, "error": error})
