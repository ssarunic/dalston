"""Durable event delivery using Redis Streams.

Provides optional durability for events that need guaranteed delivery:
- Events are written to both pub/sub (real-time) and Streams (durable)
- On orchestrator startup, unprocessed events are replayed from the Stream
- Consumer groups ensure exactly-once processing across orchestrator instances

This module supplements the existing pub/sub events with durability for
crash recovery scenarios.

Stream: dalston:events:stream
Consumer group: orchestrators
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

import dalston.telemetry

logger = structlog.get_logger()

# Durable events stream
EVENTS_STREAM = "dalston:events:stream"
EVENTS_CONSUMER_GROUP = "orchestrators"

# Stream configuration
MAX_STREAM_LENGTH = 10000  # Approximate max entries (uses MAXLEN ~)
EVENT_TTL_HOURS = 24  # Events older than this can be trimmed


def _json_serializer(obj: Any) -> str:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def ensure_events_stream_group(redis: Redis) -> None:
    """Create the events consumer group if it doesn't exist.

    Creates the stream and consumer group atomically. Safe to call
    multiple times (idempotent).

    Args:
        redis: Async Redis client
    """
    try:
        await redis.xgroup_create(
            EVENTS_STREAM,
            EVENTS_CONSUMER_GROUP,
            id="0",  # Start from beginning of stream
            mkstream=True,
        )
        logger.debug(
            "events_stream_group_created",
            stream=EVENTS_STREAM,
            group=EVENTS_CONSUMER_GROUP,
        )
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
        # Group already exists - this is fine


async def add_durable_event(
    redis: Redis,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """Add an event to the durable events stream.

    This should be called alongside publish_event() for events that
    need guaranteed delivery.

    Args:
        redis: Async Redis client
        event_type: Event type (e.g., "job.created", "task.completed")
        payload: Event payload

    Returns:
        Redis message ID
    """
    event = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": json.dumps(payload, default=_json_serializer),
    }

    # Inject trace context for distributed tracing
    trace_context = dalston.telemetry.inject_trace_context()
    if trace_context:
        event["_trace_context"] = json.dumps(trace_context)

    # Add to stream with approximate max length (allows some overflow)
    message_id = await redis.xadd(
        EVENTS_STREAM,
        event,  # type: ignore[arg-type]
        maxlen=MAX_STREAM_LENGTH,
        approximate=True,
    )

    logger.debug(
        "durable_event_added",
        stream=EVENTS_STREAM,
        message_id=message_id,
        event_type=event_type,
    )

    return message_id


async def read_pending_events(
    redis: Redis,
    consumer: str,
    count: int = 100,
) -> list[dict[str, Any]]:
    """Read pending events that haven't been ACKed.

    Used on startup to process any events that were delivered but not
    acknowledged (e.g., due to crash).

    Args:
        redis: Async Redis client
        consumer: Consumer ID (typically orchestrator instance ID)
        count: Maximum events to read

    Returns:
        List of event dicts with 'id', 'type', and payload fields
    """
    await ensure_events_stream_group(redis)

    try:
        # XREADGROUP with "0" reads our pending entries (not yet ACKed)
        results = await redis.xreadgroup(
            EVENTS_CONSUMER_GROUP,
            consumer,
            {EVENTS_STREAM: "0"},  # "0" = pending entries only
            count=count,
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            await ensure_events_stream_group(redis)
            return []
        raise

    if not results:
        return []

    events = []
    for _stream_name, messages in results:
        for msg_id, fields in messages:
            try:
                event = {
                    "id": msg_id,
                    "type": fields.get("type", "unknown"),
                    "timestamp": fields.get("timestamp"),
                }
                # Parse the nested payload JSON
                payload_str = fields.get("payload", "{}")
                event.update(json.loads(payload_str))
                events.append(event)
            except (json.JSONDecodeError, TypeError):
                logger.warning("invalid_event_payload", message_id=msg_id)
                continue

    return events


async def read_new_events(
    redis: Redis,
    consumer: str,
    count: int = 10,
    block_ms: int = 1000,
) -> list[dict[str, Any]]:
    """Read new events from the stream.

    Used for consuming events in a loop. Returns new (undelivered) events.

    Args:
        redis: Async Redis client
        consumer: Consumer ID
        count: Maximum events to read
        block_ms: How long to block waiting for events

    Returns:
        List of event dicts with 'id', 'type', and payload fields
    """
    await ensure_events_stream_group(redis)

    try:
        # XREADGROUP with ">" reads only NEW entries
        results = await redis.xreadgroup(
            EVENTS_CONSUMER_GROUP,
            consumer,
            {EVENTS_STREAM: ">"},  # ">" = only new entries
            count=count,
            block=block_ms,
        )
    except ResponseError as e:
        if "NOGROUP" in str(e):
            await ensure_events_stream_group(redis)
            return []
        raise

    if not results:
        return []

    events = []
    for _stream_name, messages in results:
        for msg_id, fields in messages:
            try:
                event = {
                    "id": msg_id,
                    "type": fields.get("type", "unknown"),
                    "timestamp": fields.get("timestamp"),
                }
                payload_str = fields.get("payload", "{}")
                event.update(json.loads(payload_str))
                events.append(event)
            except (json.JSONDecodeError, TypeError):
                logger.warning("invalid_event_payload", message_id=msg_id)
                continue

    return events


async def ack_event(redis: Redis, message_id: str) -> None:
    """Acknowledge an event as processed.

    Args:
        redis: Async Redis client
        message_id: Redis message ID to acknowledge
    """
    await redis.xack(EVENTS_STREAM, EVENTS_CONSUMER_GROUP, message_id)
    logger.debug("event_acked", stream=EVENTS_STREAM, message_id=message_id)


async def get_stream_info(redis: Redis) -> dict[str, Any]:
    """Get information about the events stream for monitoring.

    Args:
        redis: Async Redis client

    Returns:
        Dict with stream_length, pending_count, consumers, etc.
    """
    info: dict[str, Any] = {
        "stream_key": EVENTS_STREAM,
        "stream_length": 0,
        "pending_count": 0,
        "consumers": [],
    }

    try:
        length = await redis.xlen(EVENTS_STREAM)
        info["stream_length"] = length

        pending_summary = await redis.xpending(EVENTS_STREAM, EVENTS_CONSUMER_GROUP)
        if pending_summary:
            info["pending_count"] = pending_summary.get("pending", 0)
            consumers = pending_summary.get("consumers", [])
            info["consumers"] = [
                {"name": c["name"], "pending": c["pending"]} for c in consumers
            ]

    except ResponseError as e:
        if "NOGROUP" not in str(e):
            raise

    return info
