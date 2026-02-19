"""Redis Streams helper functions for durable task queues.

Provides abstractions over Redis Streams for:
- Task queue management with delivery tracking
- Consumer group coordination
- Stale task detection and recovery

Stream naming pattern: dalston:stream:{stage}
Consumer group: "engines" (created on first use)

Example usage:
    # Producer (orchestrator)
    msg_id = await add_task(redis, "transcribe", task_id, job_id, timeout_s=600)

    # Consumer (engine)
    msg = await read_task(redis, "transcribe", consumer="engine-1")
    if msg:
        process(msg.task_id)
        await ack_task(redis, "transcribe", msg.id)

    # Recovery (engine startup)
    stale = await claim_stale_tasks(redis, "transcribe", "engine-2", min_idle_ms=600000)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from dalston.common.streams_types import (
    CONSUMER_GROUP,
    HEARTBEAT_TIMEOUT_SECONDS,
    JOB_CANCELLED_KEY_PREFIX,
    JOB_CANCELLED_TTL_SECONDS,
    STREAM_PREFIX,
    PendingTask,
    StreamMessage,
)

logger = structlog.get_logger()

# Re-export for backwards compatibility
__all__ = [
    "STREAM_PREFIX",
    "CONSUMER_GROUP",
    "JOB_CANCELLED_KEY_PREFIX",
    "JOB_CANCELLED_TTL_SECONDS",
    "StreamMessage",
    "PendingTask",
]


def _base_stage(stage: str) -> str:
    """Extract base stage from per-channel stage name.

    Per-channel stages like "transcribe_ch0" route to the base "transcribe" stream
    so that any engine listening on the base stream can process them.

    Examples:
        transcribe_ch0 -> transcribe
        transcribe_ch1 -> transcribe
        diarize -> diarize (unchanged)
    """
    if "_ch" in stage and stage.split("_ch")[-1].isdigit():
        return stage.rsplit("_ch", 1)[0]
    return stage


def _stream_key(stage: str) -> str:
    """Build stream key from stage name.

    Per-channel stages route to the base stream so engines don't need
    separate consumers for each channel.
    """
    return f"{STREAM_PREFIX}{_base_stage(stage)}"


async def ensure_stream_group(redis: Redis, stage: str) -> None:
    """Create consumer group if it doesn't exist.

    Creates the stream and consumer group atomically. Safe to call
    multiple times (idempotent).

    Args:
        redis: Async Redis client
        stage: Pipeline stage name (e.g., "transcribe")
    """
    stream_key = _stream_key(stage)
    try:
        await redis.xgroup_create(
            stream_key,
            CONSUMER_GROUP,
            id="0",  # Start from beginning of stream
            mkstream=True,  # Create stream if it doesn't exist
        )
        logger.debug("stream_group_created", stream=stream_key, group=CONSUMER_GROUP)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
        # Group already exists - this is fine


async def add_task(
    redis: Redis,
    stage: str,
    task_id: str,
    job_id: str,
    timeout_s: int,
) -> str:
    """Add task to stream.

    Args:
        redis: Async Redis client
        stage: Pipeline stage name
        task_id: Task UUID string
        job_id: Job UUID string
        timeout_s: Task timeout in seconds (for scanner to detect stale tasks)

    Returns:
        Redis message ID (e.g., "1234567890-0")
    """
    stream_key = _stream_key(stage)

    # Ensure consumer group exists before adding
    await ensure_stream_group(redis, stage)

    now = datetime.now(UTC)
    timeout_at = datetime.fromtimestamp(now.timestamp() + timeout_s, tz=UTC)

    fields = {
        "task_id": task_id,
        "job_id": job_id,
        "enqueued_at": now.isoformat(),
        "timeout_at": timeout_at.isoformat(),
    }

    message_id = await redis.xadd(stream_key, fields)  # type: ignore[arg-type]

    logger.debug(
        "task_added_to_stream",
        stream=stream_key,
        message_id=message_id,
        task_id=task_id,
    )

    return message_id


async def read_task(
    redis: Redis,
    stage: str,
    consumer: str,
    block_ms: int = 30000,
) -> StreamMessage | None:
    """Read next available task from stream.

    Blocks until a task is available or timeout expires. The task is
    automatically added to the Pending Entries List (PEL) upon delivery.

    Args:
        redis: Async Redis client
        stage: Pipeline stage name
        consumer: Consumer ID (typically engine_id)
        block_ms: How long to block waiting for tasks (default 30s)

    Returns:
        StreamMessage if a task was read, None on timeout
    """
    stream_key = _stream_key(stage)

    # Ensure consumer group exists
    await ensure_stream_group(redis, stage)

    try:
        results = await redis.xreadgroup(
            CONSUMER_GROUP,
            consumer,
            {stream_key: ">"},  # Only new (undelivered) messages
            count=1,
            block=block_ms,
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            # Group doesn't exist yet - create and retry
            await ensure_stream_group(redis, stage)
            return None
        raise

    if not results:
        return None

    # Results format: [[stream_name, [(msg_id, {fields})]]]
    for _stream_name, messages in results:
        for msg_id, fields in messages:
            return _parse_message(msg_id, fields, delivery_count=1)

    return None


async def claim_stale_tasks(
    redis: Redis,
    stage: str,
    consumer: str,
    min_idle_ms: int,
    count: int = 1,
) -> list[StreamMessage]:
    """Claim tasks that have been idle longer than min_idle_ms.

    Uses XAUTOCLAIM to atomically find and claim stale tasks. The delivery
    count is automatically incremented.

    Args:
        redis: Async Redis client
        stage: Pipeline stage name
        consumer: Consumer ID claiming the tasks
        min_idle_ms: Minimum idle time in milliseconds
        count: Maximum number of tasks to claim

    Returns:
        List of claimed StreamMessages (may be empty)
    """
    stream_key = _stream_key(stage)

    try:
        # XAUTOCLAIM returns: [next_start_id, [[id, fields], ...], [deleted_ids]]
        results = await redis.xautoclaim(
            stream_key,
            CONSUMER_GROUP,
            consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            return []
        raise

    if not results or len(results) < 2:
        return []

    messages = []
    claimed_entries = results[1]

    for entry in claimed_entries:
        if entry is None or len(entry) < 2:
            continue
        msg_id, fields = entry[0], entry[1]
        if fields:  # Skip deleted/nil messages
            # Get actual delivery count from XPENDING for this message
            pending_info = await _get_pending_entry(redis, stage, msg_id)
            delivery_count = pending_info.delivery_count if pending_info else 1
            messages.append(_parse_message(msg_id, fields, delivery_count))

    if messages:
        logger.info(
            "claimed_stale_tasks",
            stream=stream_key,
            consumer=consumer,
            count=len(messages),
            task_ids=[m.task_id for m in messages],
        )

    return messages


async def claim_tasks_by_id(
    redis: Redis,
    stage: str,
    consumer: str,
    message_ids: list[str],
) -> list[StreamMessage]:
    """Claim specific messages by ID.

    Used for selective recovery when we know which messages to claim
    (e.g., from dead engines).

    Args:
        redis: Async Redis client
        stage: Pipeline stage name
        consumer: Consumer ID claiming the tasks
        message_ids: List of message IDs to claim

    Returns:
        List of claimed StreamMessages
    """
    if not message_ids:
        return []

    stream_key = _stream_key(stage)

    try:
        # XCLAIM returns: [[id, fields], ...]
        results = await redis.xclaim(
            stream_key,
            CONSUMER_GROUP,
            consumer,
            min_idle_time=0,  # Claim regardless of idle time
            message_ids=message_ids,  # type: ignore[arg-type]
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            return []
        raise

    messages = []
    for entry in results:
        if entry is None or len(entry) < 2:
            continue
        msg_id, fields = entry[0], entry[1]
        if fields:
            pending_info = await _get_pending_entry(redis, stage, msg_id)
            delivery_count = pending_info.delivery_count if pending_info else 1
            messages.append(_parse_message(msg_id, fields, delivery_count))

    return messages


async def ack_task(redis: Redis, stage: str, message_id: str) -> None:
    """Acknowledge task completion.

    Removes the message from the Pending Entries List (PEL). Should be
    called after task processing completes (success or failure).

    Args:
        redis: Async Redis client
        stage: Pipeline stage name
        message_id: Redis message ID to acknowledge
    """
    stream_key = _stream_key(stage)
    await redis.xack(stream_key, CONSUMER_GROUP, message_id)
    logger.debug("task_acked", stream=stream_key, message_id=message_id)


async def get_pending(redis: Redis, stage: str) -> list[PendingTask]:
    """Get all pending tasks with metadata.

    Returns information about all tasks currently being processed
    (in the Pending Entries List).

    Args:
        redis: Async Redis client
        stage: Pipeline stage name

    Returns:
        List of PendingTask objects with idle time and delivery count
    """
    stream_key = _stream_key(stage)

    try:
        # XPENDING with detail returns: [[id, consumer, idle_ms, deliveries], ...]
        results = await redis.xpending_range(
            stream_key,
            CONSUMER_GROUP,
            min="-",
            max="+",
            count=1000,  # Reasonable limit
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            return []
        raise

    if not results:
        return []

    pending_tasks = []
    for entry in results:
        msg_id = entry["message_id"]
        consumer = entry["consumer"]
        idle_ms = entry["time_since_delivered"]
        delivery_count = entry["times_delivered"]

        # Get the task_id from the message itself
        messages = await redis.xrange(stream_key, min=msg_id, max=msg_id, count=1)
        if messages:
            _, fields = messages[0]
            task_id = fields.get("task_id", "unknown")
        else:
            task_id = "unknown"

        pending_tasks.append(
            PendingTask(
                message_id=msg_id,
                task_id=task_id,
                consumer=consumer,
                idle_ms=idle_ms,
                delivery_count=delivery_count,
            )
        )

    return pending_tasks


async def _get_pending_entry(
    redis: Redis, stage: str, message_id: str
) -> PendingTask | None:
    """Get pending entry for a specific message ID."""
    stream_key = _stream_key(stage)

    try:
        results = await redis.xpending_range(
            stream_key,
            CONSUMER_GROUP,
            min=message_id,
            max=message_id,
            count=1,
        )
    except ResponseError:
        return None

    if not results:
        return None

    entry = results[0]
    return PendingTask(
        message_id=entry["message_id"],
        task_id="",  # Not needed for delivery count lookup
        consumer=entry["consumer"],
        idle_ms=entry["time_since_delivered"],
        delivery_count=entry["times_delivered"],
    )


async def discover_streams(redis: Redis) -> list[str]:
    """Discover all task streams via SCAN.

    Finds all streams matching the dalston:stream:* pattern.

    Args:
        redis: Async Redis client

    Returns:
        List of stream keys (e.g., ["dalston:stream:transcribe", ...])
    """
    streams: list[str] = []
    cursor = 0

    while True:
        cursor, keys = await redis.scan(
            cursor,
            match=f"{STREAM_PREFIX}*",
            count=100,
        )
        streams.extend(keys)
        if cursor == 0:
            break

    return streams


async def get_stream_info(redis: Redis, stage: str) -> dict[str, Any]:
    """Get stream statistics for monitoring.

    Args:
        redis: Async Redis client
        stage: Pipeline stage name

    Returns:
        Dict with stream_length, pending_count, consumers, etc.
    """
    stream_key = _stream_key(stage)

    info: dict[str, Any] = {
        "stream_key": stream_key,
        "stream_length": 0,
        "pending_count": 0,
        "consumers": [],
    }

    try:
        # Get stream length
        length = await redis.xlen(stream_key)
        info["stream_length"] = length

        # Get pending summary
        pending_summary = await redis.xpending(stream_key, CONSUMER_GROUP)
        if pending_summary:
            info["pending_count"] = pending_summary.get("pending", 0)
            consumers = pending_summary.get("consumers", [])
            info["consumers"] = [
                {"name": c["name"], "pending": c["pending"]} for c in consumers
            ]

    except ResponseError as e:
        if "NOGROUP" not in str(e):
            raise
        # Stream or group doesn't exist yet

    return info


async def is_engine_alive(redis: Redis, engine_id: str) -> bool:
    """Check if an engine is still alive (heartbeating).

    Args:
        redis: Async Redis client
        engine_id: Engine ID to check

    Returns:
        True if engine has recent heartbeat, False otherwise
    """
    from dalston.engine_sdk.registry import ENGINE_KEY_PREFIX

    engine_key = f"{ENGINE_KEY_PREFIX}{engine_id}"
    data: dict = await redis.hgetall(engine_key)  # type: ignore[assignment]

    if not data:
        return False

    # Check status
    status = data.get("status", "offline")
    if status == "offline":
        return False

    # Check heartbeat freshness (60 second timeout)
    last_heartbeat_str = data.get("last_heartbeat")
    if not last_heartbeat_str:
        return False

    try:
        last_heartbeat = datetime.fromisoformat(
            last_heartbeat_str.replace("Z", "+00:00")
        )
        age = (datetime.now(UTC) - last_heartbeat).total_seconds()
        return age < HEARTBEAT_TIMEOUT_SECONDS
    except ValueError:
        return False


async def mark_job_cancelled(redis: Redis, job_id: str) -> None:
    """Mark a job as cancelled in Redis.

    Sets a key that engines can check to skip processing tasks.
    The key has a TTL to auto-cleanup after the job would have expired anyway.

    Args:
        redis: Async Redis client
        job_id: Job UUID string
    """
    key = f"{JOB_CANCELLED_KEY_PREFIX}{job_id}"
    await redis.set(key, "1", ex=JOB_CANCELLED_TTL_SECONDS)
    logger.debug("job_marked_cancelled", job_id=job_id)


async def is_job_cancelled(redis: Redis, job_id: str) -> bool:
    """Check if a job has been cancelled.

    Args:
        redis: Async Redis client
        job_id: Job UUID string

    Returns:
        True if job is cancelled, False otherwise
    """
    key = f"{JOB_CANCELLED_KEY_PREFIX}{job_id}"
    return await redis.exists(key) > 0


def _parse_message(
    msg_id: str,
    fields: dict[str, str],
    delivery_count: int,
) -> StreamMessage:
    """Parse Redis message fields into StreamMessage."""
    task_id = fields.get("task_id", "")
    job_id = fields.get("job_id", "")

    enqueued_str = fields.get("enqueued_at", "")
    timeout_str = fields.get("timeout_at", "")

    try:
        enqueued_at = datetime.fromisoformat(enqueued_str)
    except (ValueError, TypeError):
        enqueued_at = datetime.now(UTC)

    try:
        timeout_at = datetime.fromisoformat(timeout_str)
    except (ValueError, TypeError):
        timeout_at = datetime.now(UTC)

    return StreamMessage(
        id=msg_id,
        task_id=task_id,
        job_id=job_id,
        enqueued_at=enqueued_at,
        timeout_at=timeout_at,
        delivery_count=delivery_count,
    )
