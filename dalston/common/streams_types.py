"""Shared types and constants for Redis Streams task queues.

This module provides common dataclasses and constants used by both
the async (streams.py) and sync (streams_sync.py) implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Stream naming
STREAM_PREFIX = "dalston:stream:"
CONSUMER_GROUP = "engines"

# Job cancellation tracking
JOB_CANCELLED_KEY_PREFIX = "dalston:job:cancelled:"
JOB_CANCELLED_TTL_SECONDS = 3600 * 24  # 24 hours - longer than any job could run
WAITING_ENGINE_TASKS_KEY = "dalston:waiting_engine_tasks"

# Recovery thresholds
STALE_THRESHOLD_MS = 10 * 60 * 1000  # 10 minutes
MAX_DELIVERIES = 3

# Heartbeat timeout for engine health checks (must match registry)
HEARTBEAT_TIMEOUT_SECONDS = 60


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
