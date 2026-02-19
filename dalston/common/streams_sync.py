"""Synchronous Redis Streams helpers for batch engines.

This module provides synchronous versions of the streams.py functions
for use in the engine SDK, which uses synchronous Redis.

Stream naming pattern: dalston:stream:{stage}
Consumer group: "engines"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import redis
import structlog
from redis.exceptions import ResponseError

logger = structlog.get_logger()

# Stream naming (must match async version)
STREAM_PREFIX = "dalston:stream:"
CONSUMER_GROUP = "engines"

# Recovery thresholds
STALE_THRESHOLD_MS = 10 * 60 * 1000  # 10 minutes
MAX_DELIVERIES = 3


@dataclass
class StreamMessage:
    """Message read from a Redis Stream.

    Attributes:
        id: Redis message ID (e.g., "1234567890-0")
        task_id: Task UUID string
        job_id: Job UUID string
        enqueued_at: When the task was added to the stream
        timeout_at: When the task should be considered timed out
        delivery_count: How many times this message was delivered (1 = first attempt)
    """

    id: str
    task_id: str
    job_id: str
    enqueued_at: datetime
    timeout_at: datetime
    delivery_count: int


@dataclass
class PendingTask:
    """Task currently being processed (in Pending Entries List).

    Attributes:
        message_id: Redis message ID
        task_id: Task UUID string
        consumer: Engine ID that claimed it
        idle_ms: Time since last delivery in milliseconds
        delivery_count: How many times delivered
    """

    message_id: str
    task_id: str
    consumer: str
    idle_ms: int
    delivery_count: int


def _stream_key(stage: str) -> str:
    """Build stream key from stage name."""
    return f"{STREAM_PREFIX}{stage}"


def ensure_stream_group(r: redis.Redis, stage: str) -> None:
    """Create consumer group if it doesn't exist.

    Args:
        r: Sync Redis client
        stage: Pipeline stage name (e.g., "transcribe")
    """
    stream_key = _stream_key(stage)
    try:
        r.xgroup_create(
            stream_key,
            CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
        logger.debug("stream_group_created", stream=stream_key, group=CONSUMER_GROUP)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def read_task(
    r: redis.Redis,
    stage: str,
    consumer: str,
    block_ms: int = 30000,
) -> StreamMessage | None:
    """Read next available task from stream.

    Blocks until a task is available or timeout expires.

    Args:
        r: Sync Redis client
        stage: Pipeline stage name
        consumer: Consumer ID (typically engine_id)
        block_ms: How long to block waiting for tasks (default 30s)

    Returns:
        StreamMessage if a task was read, None on timeout
    """
    stream_key = _stream_key(stage)

    ensure_stream_group(r, stage)

    try:
        results = r.xreadgroup(
            CONSUMER_GROUP,
            consumer,
            {stream_key: ">"},
            count=1,
            block=block_ms,
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            ensure_stream_group(r, stage)
            return None
        raise

    if not results:
        return None

    # Results format: [[stream_name, [(msg_id, {fields})]]]
    for _stream_name, messages in results:  # type: ignore[union-attr]
        for msg_id, fields in messages:
            return _parse_message(msg_id, fields, delivery_count=1)

    return None


def get_pending(r: redis.Redis, stage: str) -> list[PendingTask]:
    """Get all pending tasks with metadata.

    Args:
        r: Sync Redis client
        stage: Pipeline stage name

    Returns:
        List of PendingTask objects
    """
    stream_key = _stream_key(stage)

    try:
        results = r.xpending_range(
            stream_key,
            CONSUMER_GROUP,
            min="-",
            max="+",
            count=1000,
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            return []
        raise

    if not results:
        return []

    pending_tasks = []
    for entry in results:  # type: ignore[union-attr]
        msg_id = entry["message_id"]
        consumer = entry["consumer"]
        idle_ms = entry["time_since_delivered"]
        delivery_count = entry["times_delivered"]

        # Get the task_id from the message itself
        messages = r.xrange(stream_key, min=msg_id, max=msg_id, count=1)
        if messages:
            _, fields = messages[0]  # type: ignore[index]
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


def claim_tasks_by_id(
    r: redis.Redis,
    stage: str,
    consumer: str,
    message_ids: list[str],
) -> list[StreamMessage]:
    """Claim specific messages by ID.

    Args:
        r: Sync Redis client
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
        results = r.xclaim(
            stream_key,
            CONSUMER_GROUP,
            consumer,
            min_idle_time=0,
            message_ids=message_ids,  # type: ignore[arg-type]
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            return []
        raise

    messages = []
    for entry in results:  # type: ignore[union-attr]
        if entry is None or len(entry) < 2:
            continue
        msg_id, fields = entry[0], entry[1]
        if fields:
            pending_info = _get_pending_entry(r, stage, msg_id)
            delivery_count = pending_info.delivery_count if pending_info else 1
            messages.append(_parse_message(msg_id, fields, delivery_count))

    return messages


def _get_pending_entry(
    r: redis.Redis, stage: str, message_id: str
) -> PendingTask | None:
    """Get pending entry for a specific message ID."""
    stream_key = _stream_key(stage)

    try:
        results = r.xpending_range(
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

    entry = results[0]  # type: ignore[index]
    return PendingTask(
        message_id=entry["message_id"],
        task_id="",
        consumer=entry["consumer"],
        idle_ms=entry["time_since_delivered"],
        delivery_count=entry["times_delivered"],
    )


def ack_task(r: redis.Redis, stage: str, message_id: str) -> None:
    """Acknowledge task completion.

    Removes the message from the Pending Entries List (PEL).

    Args:
        r: Sync Redis client
        stage: Pipeline stage name
        message_id: Redis message ID to acknowledge
    """
    stream_key = _stream_key(stage)
    r.xack(stream_key, CONSUMER_GROUP, message_id)
    logger.debug("task_acked", stream=stream_key, message_id=message_id)


def is_engine_alive(r: redis.Redis, engine_id: str) -> bool:
    """Check if an engine is still alive (heartbeating).

    Args:
        r: Sync Redis client
        engine_id: Engine ID to check

    Returns:
        True if engine has recent heartbeat, False otherwise
    """
    from dalston.engine_sdk.registry import ENGINE_KEY_PREFIX

    engine_key = f"{ENGINE_KEY_PREFIX}{engine_id}"
    data: dict = r.hgetall(engine_key)  # type: ignore[assignment]

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
        return age < 60  # HEARTBEAT_TIMEOUT_SECONDS
    except ValueError:
        return False


def claim_stale_from_dead_engines(
    r: redis.Redis,
    stage: str,
    consumer: str,
    min_idle_ms: int = STALE_THRESHOLD_MS,
    count: int = 1,
) -> list[StreamMessage]:
    """Claim tasks from engines that are no longer heartbeating.

    Only claims tasks that have been idle for min_idle_ms AND whose
    owning engine is no longer alive.

    Args:
        r: Sync Redis client
        stage: Pipeline stage name
        consumer: Consumer ID claiming the tasks
        min_idle_ms: Minimum idle time in milliseconds
        count: Maximum number of tasks to claim

    Returns:
        List of claimed StreamMessages
    """
    pending = get_pending(r, stage)
    claimable: list[str] = []

    for task in pending:
        if task.idle_ms < min_idle_ms:
            continue

        # Check if the engine that has this task is still alive
        engine_alive = is_engine_alive(r, task.consumer)

        if not engine_alive:
            claimable.append(task.message_id)
            if len(claimable) >= count:
                break

    if not claimable:
        return []

    return claim_tasks_by_id(r, stage, consumer, claimable)


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
