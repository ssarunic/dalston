"""Durable orchestrator event delivery using Redis Streams.

Provides durable consumption helpers for orchestrator events, including:
- consumer-group setup and stream reads
- delivery-count-aware stale-claim recovery
- typed event envelope parsing/validation
- dead-letter quarantine helpers with loss-averse ordering (XADD before XACK)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import ResponseError

import dalston.telemetry

logger = structlog.get_logger()

# Durable events stream configuration
EVENTS_STREAM = "dalston:events:stream"
EVENTS_CONSUMER_GROUP = "orchestrators"
EVENTS_DLQ_STREAM = "dalston:events:dlq"

# Stream configuration
MAX_STREAM_LENGTH = 10000  # Approximate max entries (uses MAXLEN ~)
DEFAULT_DLQ_MAX_LENGTH = 10000  # Approximate max entries for DLQ stream
EVENT_TTL_HOURS = 24  # Events older than this can be trimmed

# M54 failure reason taxonomy
FAILURE_REASON_INVALID_PAYLOAD_JSON = "invalid_payload_json"
FAILURE_REASON_INVALID_EVENT_SCHEMA = "invalid_event_schema"
FAILURE_REASON_UNKNOWN_EVENT_TYPE = "unknown_event_type"
FAILURE_REASON_HANDLER_EXCEPTION = "handler_exception"
FAILURE_REASON_DISPATCH_ERROR = "dispatch_error"

NON_RETRYABLE_FAILURE_REASONS = {
    FAILURE_REASON_INVALID_PAYLOAD_JSON,
    FAILURE_REASON_INVALID_EVENT_SCHEMA,
    FAILURE_REASON_UNKNOWN_EVENT_TYPE,
}


class DurableEventEnvelope(BaseModel):
    """Typed envelope for events consumed from the durable stream."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    delivery_count: int = 1
    raw_fields: dict[str, str] = Field(default_factory=dict)
    event_type: str | None = None
    timestamp: str | None = None
    payload: dict[str, Any] | None = None
    raw_payload: str | None = None
    trace_context: dict[str, Any] | None = None
    failure_reason: str | None = None
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        """Whether this envelope passed parse/schema validation."""
        return self.failure_reason is None

    def to_event_dict(self) -> dict[str, Any]:
        """Convert a valid envelope to the handler dispatch payload shape."""
        if not self.is_valid:
            raise ValueError(
                f"Cannot dispatch invalid durable event envelope: {self.failure_reason}"
            )
        if self.event_type is None or self.payload is None:
            raise ValueError(
                "Durable event envelope is missing required dispatch fields"
            )

        event: dict[str, Any] = {"type": self.event_type, **self.payload}
        if self.timestamp is not None:
            event["timestamp"] = self.timestamp
        if self.trace_context:
            event["_trace_context"] = self.trace_context
        return event


def _json_serializer(obj: Any) -> str:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _coerce_to_text(value: Any) -> str:
    """Convert Redis field values to text for stable logging/DLQ metadata."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, default=_json_serializer)
    return str(value)


def _coerce_to_int(value: Any, default: int = 1) -> int:
    """Safely parse an integer value."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_stream_fields(fields: dict[Any, Any]) -> dict[str, str]:
    """Normalize Redis stream fields into a text dict."""
    return {
        _coerce_to_text(key): _coerce_to_text(value) for key, value in fields.items()
    }


def _extract_times_delivered_from_entry(entry: Any) -> int | None:
    """Extract delivery count from a message entry when metadata is available."""
    if not isinstance(entry, list | tuple):
        return None

    # Some Redis clients/extensions may attach metadata in an additional element.
    if len(entry) >= 3 and isinstance(entry[2], dict):
        value = entry[2].get("times_delivered")
        if value is not None:
            return _coerce_to_int(value, default=1)

    return None


async def _lookup_delivery_count(
    redis: Redis,
    message_id: str,
    *,
    default: int = 1,
) -> int:
    """Lookup delivery count using XPENDING metadata as fallback."""
    try:
        pending_entries = await redis.xpending_range(
            EVENTS_STREAM,
            EVENTS_CONSUMER_GROUP,
            min=message_id,
            max=message_id,
            count=1,
        )
    except ResponseError:
        return default

    if not pending_entries:
        return default

    entry = pending_entries[0]
    if isinstance(entry, dict):
        value = (
            entry.get("times_delivered")
            or entry.get("delivery_count")
            or entry.get("deliveries")
        )
        if value is not None:
            return _coerce_to_int(value, default=default)

    if isinstance(entry, list | tuple) and len(entry) >= 4:
        return _coerce_to_int(entry[3], default=default)

    return default


def _parse_trace_context(raw_trace_context: str | None) -> dict[str, Any] | None:
    """Parse optional trace-context JSON payload from stream fields."""
    if raw_trace_context is None:
        return None

    try:
        parsed = json.loads(raw_trace_context)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _parse_envelope(
    message_id: str,
    fields: dict[Any, Any],
    *,
    delivery_count: int,
) -> DurableEventEnvelope:
    """Parse one raw stream message into a typed envelope."""
    normalized = _normalize_stream_fields(fields)
    event_type = normalized.get("type") or None
    timestamp = normalized.get("timestamp") or None
    raw_payload = normalized.get("payload")
    trace_context = _parse_trace_context(normalized.get("_trace_context"))

    base_kwargs = {
        "message_id": message_id,
        "delivery_count": max(delivery_count, 1),
        "raw_fields": normalized,
        "event_type": event_type,
        "timestamp": timestamp,
        "raw_payload": raw_payload,
        "trace_context": trace_context,
    }

    if not event_type:
        return DurableEventEnvelope(
            **base_kwargs,
            failure_reason=FAILURE_REASON_INVALID_EVENT_SCHEMA,
            error="missing_or_invalid_type",
        )

    if raw_payload is None:
        return DurableEventEnvelope(
            **base_kwargs,
            failure_reason=FAILURE_REASON_INVALID_EVENT_SCHEMA,
            error="missing_payload_field",
        )

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        return DurableEventEnvelope(
            **base_kwargs,
            failure_reason=FAILURE_REASON_INVALID_PAYLOAD_JSON,
            error=str(exc),
        )

    if not isinstance(payload, dict):
        return DurableEventEnvelope(
            **base_kwargs,
            failure_reason=FAILURE_REASON_INVALID_EVENT_SCHEMA,
            error="payload_must_be_json_object",
        )

    return DurableEventEnvelope(
        **base_kwargs,
        payload=payload,
    )


def _parse_xautoclaim_messages(result: Any) -> tuple[str, list[Any]]:
    """Normalize xautoclaim return shape across redis client variants."""
    if not result:
        return "0-0", []

    if isinstance(result, list | tuple):
        if len(result) < 2:
            return "0-0", []
        cursor = _coerce_to_text(result[0]) or "0-0"
        messages = result[1] if isinstance(result[1], list) else []
        return cursor, messages

    return "0-0", []


async def ensure_events_stream_group(redis: Redis) -> None:
    """Create the events consumer group if it doesn't exist."""
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


async def add_durable_event(
    redis: Redis,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """Add an event to the durable events stream."""
    event = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": json.dumps(payload, default=_json_serializer),
    }

    # Inject trace context for distributed tracing
    trace_context = dalston.telemetry.inject_trace_context()
    if trace_context:
        event["_trace_context"] = json.dumps(trace_context)

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


async def claim_stale_pending_events(
    redis: Redis,
    consumer: str,
    min_idle_ms: int = 60000,
    count: int = 100,
    max_iterations: int = 10,
) -> list[DurableEventEnvelope]:
    """Claim pending events that have been idle too long."""
    await ensure_events_stream_group(redis)

    all_events: list[DurableEventEnvelope] = []
    cursor = "0-0"
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        try:
            result = await redis.xautoclaim(
                EVENTS_STREAM,
                EVENTS_CONSUMER_GROUP,
                consumer,
                min_idle_time=min_idle_ms,
                start_id=cursor,
                count=count,
            )
        except ResponseError as e:
            if "NOGROUP" in str(e):
                await ensure_events_stream_group(redis)
                return all_events
            raise

        next_cursor, messages = _parse_xautoclaim_messages(result)

        if not messages:
            break

        for entry in messages:
            if not isinstance(entry, list | tuple) or len(entry) < 2:
                continue

            msg_id = _coerce_to_text(entry[0])
            fields = entry[1]
            if not msg_id or not isinstance(fields, dict):
                continue

            delivery_count = _extract_times_delivered_from_entry(entry)
            if delivery_count is None:
                delivery_count = await _lookup_delivery_count(redis, msg_id, default=1)

            all_events.append(
                _parse_envelope(
                    msg_id,
                    fields,
                    delivery_count=delivery_count,
                )
            )

        if next_cursor == "0-0":
            break

        cursor = next_cursor

    return all_events


async def read_new_events(
    redis: Redis,
    consumer: str,
    count: int = 10,
    block_ms: int = 1000,
) -> list[DurableEventEnvelope]:
    """Read new events from the stream."""
    await ensure_events_stream_group(redis)

    try:
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

    events: list[DurableEventEnvelope] = []
    for _stream_name, messages in results:
        for msg_id, fields in messages:
            if not isinstance(fields, dict):
                continue
            events.append(
                _parse_envelope(
                    _coerce_to_text(msg_id),
                    fields,
                    delivery_count=1,  # XREADGROUP ">" always delivers as first attempt
                )
            )

    return events


async def ack_event(
    redis: Redis,
    message_id: str,
    *,
    stream: str = EVENTS_STREAM,
    group: str = EVENTS_CONSUMER_GROUP,
) -> None:
    """Acknowledge an event as processed."""
    await redis.xack(stream, group, message_id)
    logger.debug("event_acked", stream=stream, group=group, message_id=message_id)


def _build_dlq_entry(
    envelope: DurableEventEnvelope,
    *,
    failure_reason: str,
    error: str | None,
    consumer_id: str | None,
    source_stream: str,
    source_group: str,
) -> dict[str, str]:
    """Build DLQ stream fields for a failed durable event."""
    event_type = envelope.event_type if envelope.event_type else "unknown"

    entry: dict[str, str] = {
        "source_stream": source_stream,
        "source_group": source_group,
        "source_message_id": envelope.message_id,
        "event_type": event_type,
        "failure_reason": failure_reason,
        "error": error or "",
        "delivery_count": str(envelope.delivery_count),
        "consumer_id": consumer_id or "",
        "failed_at": datetime.now(UTC).isoformat(),
    }

    if envelope.payload is not None:
        entry["payload"] = json.dumps(envelope.payload, default=_json_serializer)

    if envelope.raw_payload is not None:
        entry["raw_payload"] = envelope.raw_payload

    if envelope.raw_fields:
        entry["raw_fields"] = json.dumps(envelope.raw_fields, default=_json_serializer)

    return entry


async def add_event_to_dlq(
    redis: Redis,
    envelope: DurableEventEnvelope,
    *,
    failure_reason: str,
    error: str | None = None,
    consumer_id: str | None = None,
    dlq_stream: str = EVENTS_DLQ_STREAM,
    dlq_maxlen: int = DEFAULT_DLQ_MAX_LENGTH,
    source_stream: str = EVENTS_STREAM,
    source_group: str = EVENTS_CONSUMER_GROUP,
) -> str:
    """Write a failed durable event to the DLQ stream."""
    dlq_fields = _build_dlq_entry(
        envelope,
        failure_reason=failure_reason,
        error=error,
        consumer_id=consumer_id,
        source_stream=source_stream,
        source_group=source_group,
    )

    dlq_message_id = await redis.xadd(
        dlq_stream,
        dlq_fields,  # type: ignore[arg-type]
        maxlen=max(dlq_maxlen, 1),
        approximate=True,
    )

    logger.warning(
        "durable_event_written_to_dlq",
        dlq_stream=dlq_stream,
        dlq_message_id=dlq_message_id,
        source_message_id=envelope.message_id,
        event_type=envelope.event_type or "unknown",
        failure_reason=failure_reason,
        delivery_count=envelope.delivery_count,
    )

    return dlq_message_id


async def move_event_to_dlq(
    redis: Redis,
    envelope: DurableEventEnvelope,
    *,
    failure_reason: str,
    error: str | None = None,
    consumer_id: str | None = None,
    dlq_stream: str = EVENTS_DLQ_STREAM,
    dlq_maxlen: int = DEFAULT_DLQ_MAX_LENGTH,
    source_stream: str = EVENTS_STREAM,
    source_group: str = EVENTS_CONSUMER_GROUP,
) -> str:
    """Move a durable event to DLQ using loss-averse ordering.

    Ordering is intentionally:
    1. XADD to DLQ
    2. XACK in source stream
    """
    dlq_message_id = await add_event_to_dlq(
        redis,
        envelope,
        failure_reason=failure_reason,
        error=error,
        consumer_id=consumer_id,
        dlq_stream=dlq_stream,
        dlq_maxlen=dlq_maxlen,
        source_stream=source_stream,
        source_group=source_group,
    )

    await ack_event(
        redis,
        envelope.message_id,
        stream=source_stream,
        group=source_group,
    )

    logger.warning(
        "durable_event_moved_to_dlq",
        dlq_stream=dlq_stream,
        dlq_message_id=dlq_message_id,
        source_message_id=envelope.message_id,
        event_type=envelope.event_type or "unknown",
        failure_reason=failure_reason,
        delivery_count=envelope.delivery_count,
    )

    return dlq_message_id


async def get_stream_info(
    redis: Redis,
    *,
    dlq_stream: str = EVENTS_DLQ_STREAM,
) -> dict[str, Any]:
    """Get information about the durable stream and DLQ stream."""
    info: dict[str, Any] = {
        "stream_key": EVENTS_STREAM,
        "stream_length": 0,
        "pending_count": 0,
        "consumers": [],
        "dlq_stream_key": dlq_stream,
        "dlq_stream_length": 0,
    }

    try:
        info["stream_length"] = await redis.xlen(EVENTS_STREAM)
        info["dlq_stream_length"] = await redis.xlen(dlq_stream)

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


# =============================================================================
# Synchronous API for Engine SDK
# =============================================================================


def add_durable_event_sync(
    redis_client,  # redis.Redis (sync)
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """Add an event to the durable events stream (synchronous version)."""
    event = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": json.dumps(payload, default=_json_serializer),
    }

    trace_context = dalston.telemetry.inject_trace_context()
    if trace_context:
        event["_trace_context"] = json.dumps(trace_context)

    message_id = redis_client.xadd(
        EVENTS_STREAM,
        event,
        maxlen=MAX_STREAM_LENGTH,
        approximate=True,
    )

    logger.debug(
        "durable_event_added_sync",
        stream=EVENTS_STREAM,
        message_id=message_id,
        event_type=event_type,
    )

    return message_id
