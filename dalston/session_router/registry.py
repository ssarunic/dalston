"""Server-side worker registry for Session Router.

Reads worker state from Redis (written by realtime_sdk's WorkerRegistry client).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis

logger = logging.getLogger(__name__)


# Redis key patterns (shared with realtime_sdk)
WORKER_SET_KEY = "dalston:realtime:workers"
WORKER_KEY_PREFIX = "dalston:realtime:worker:"
WORKER_SESSIONS_SUFFIX = ":sessions"
SESSION_KEY_PREFIX = "dalston:realtime:session:"
ACTIVE_SESSIONS_KEY = "dalston:realtime:sessions:active"
EVENTS_CHANNEL = "dalston:realtime:events"


@dataclass
class WorkerState:
    """Worker state read from Redis.

    Attributes:
        worker_id: Unique identifier
        endpoint: WebSocket endpoint URL
        status: Current status (ready, busy, draining, offline)
        capacity: Maximum concurrent sessions
        active_sessions: Current active session count
        models_loaded: List of available model variants
        languages_supported: List of supported language codes
        gpu_memory_used: GPU memory usage string
        gpu_memory_total: Total GPU memory string
        last_heartbeat: Last heartbeat timestamp
        started_at: Worker start timestamp
    """

    worker_id: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models_loaded: list[str]
    languages_supported: list[str]
    gpu_memory_used: str
    gpu_memory_total: str
    last_heartbeat: datetime
    started_at: datetime

    @property
    def available_capacity(self) -> int:
        """Number of available session slots."""
        return max(0, self.capacity - self.active_sessions)

    @property
    def is_available(self) -> bool:
        """Whether worker can accept new sessions."""
        return self.status in ("ready", "busy") and self.available_capacity > 0


class WorkerRegistry:
    """Server-side registry for reading worker pool state.

    Used by Session Router to:
    - List all registered workers
    - Find workers with available capacity
    - Check worker health status
    - Mark workers offline

    Example:
        registry = WorkerRegistry(redis_client)

        # Get all workers
        workers = await registry.get_workers()

        # Find available workers for a request
        available = await registry.get_available_workers(
            model="fast",
            language="en"
        )

        # Mark stale worker offline
        await registry.mark_worker_offline("realtime-whisper-1")
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        """Initialize registry with Redis client.

        Args:
            redis_client: Async Redis client (shared with allocator/router)
        """
        self._redis = redis_client

    async def get_workers(self) -> list[WorkerState]:
        """Get all registered workers.

        Returns:
            List of all worker states
        """
        worker_ids = await self._redis.smembers(WORKER_SET_KEY)
        workers = []

        for worker_id in worker_ids:
            worker = await self.get_worker(worker_id)
            if worker is not None:
                workers.append(worker)

        return workers

    async def get_worker(self, worker_id: str) -> WorkerState | None:
        """Get specific worker state.

        Args:
            worker_id: Worker identifier

        Returns:
            WorkerState if found, None otherwise
        """
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        data = await self._redis.hgetall(worker_key)

        if not data:
            return None

        return self._parse_worker_state(worker_id, data)

    async def get_available_workers(
        self,
        model: str,
        language: str,
    ) -> list[WorkerState]:
        """Get workers with capacity that support requested model/language.

        Args:
            model: Model variant ("fast" or "accurate")
            language: Language code or "auto"

        Returns:
            List of available workers matching criteria, sorted by available capacity
        """
        workers = await self.get_workers()
        available = []

        for worker in workers:
            # Check status
            if not worker.is_available:
                continue

            # Check model support
            model_name = self._map_model_variant(model)
            if (
                model_name not in worker.models_loaded
                and model not in worker.models_loaded
            ):
                continue

            # Check language support
            if language != "auto" and "auto" not in worker.languages_supported:
                if language not in worker.languages_supported:
                    continue

            available.append(worker)

        # Sort by available capacity (most available first)
        available.sort(key=lambda w: w.available_capacity, reverse=True)

        return available

    async def mark_worker_offline(self, worker_id: str) -> None:
        """Mark worker as offline due to stale heartbeat.

        Args:
            worker_id: Worker identifier
        """
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        await self._redis.hset(worker_key, "status", "offline")
        logger.warning(f"Marked worker {worker_id} as offline")

    async def get_worker_session_ids(self, worker_id: str) -> set[str]:
        """Get active session IDs for a worker.

        Args:
            worker_id: Worker identifier

        Returns:
            Set of session IDs
        """
        sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"
        return await self._redis.smembers(sessions_key)

    def _parse_worker_state(self, worker_id: str, data: dict) -> WorkerState:
        """Parse worker state from Redis hash data."""
        return WorkerState(
            worker_id=worker_id,
            endpoint=data.get("endpoint", ""),
            status=data.get("status", "offline"),
            capacity=int(data.get("capacity", "0")),
            active_sessions=int(data.get("active_sessions", "0")),
            models_loaded=json.loads(data.get("models_loaded", "[]")),
            languages_supported=json.loads(data.get("languages_supported", "[]")),
            gpu_memory_used=data.get("gpu_memory_used", "0GB"),
            gpu_memory_total=data.get("gpu_memory_total", "0GB"),
            last_heartbeat=self._parse_datetime(data.get("last_heartbeat")),
            started_at=self._parse_datetime(data.get("started_at")),
        )

    def _parse_datetime(self, value: str | None) -> datetime:
        """Parse ISO datetime string."""
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)

    def _map_model_variant(self, model: str) -> str:
        """Map user-facing model name to internal name.

        Args:
            model: User-facing model name ("fast" or "accurate")

        Returns:
            Internal model name
        """
        mapping = {
            "fast": "distil-whisper",
            "accurate": "faster-whisper-large-v3",
        }
        return mapping.get(model, model)
