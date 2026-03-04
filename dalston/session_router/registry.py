"""Server-side worker registry for Session Router.

Reads worker state from Redis (written by realtime_sdk's WorkerRegistry client).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

logger = structlog.get_logger()


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
        models_loaded: List of currently loaded model variants (M43: dynamic)
        languages_supported: List of supported language codes
        engine: Engine type identifier (e.g., "parakeet", "whisper")
        runtime: Model runtime identifier (M43: e.g., "faster-whisper", "nemo")
        supports_vocabulary: Whether this engine supports vocabulary boosting
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
    engine: str
    runtime: str | None
    supports_vocabulary: bool
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
            model=None,  # None = auto, or specific model name
            language="en"
        )

        # Mark stale worker offline
        await registry.mark_worker_offline("stt-rt-transcribe-whisper-1")
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
        model: str | None,
        language: str,
        runtime: str | None = None,
        valid_runtimes: set[str] | None = None,
    ) -> list[WorkerState]:
        """Get workers with capacity that support requested model/language/runtime.

        Args:
            model: Exact model name (e.g., "parakeet-tdt-0.6b-v3") or None for any
            language: Language code or "auto"
            runtime: Model runtime (e.g., "faster-whisper") for routing when model
                     isn't pre-loaded. Workers matching runtime can load the model.
            valid_runtimes: When model=None and runtime=None, only consider workers
                     whose runtime is in this set. Used for "Any available" routing
                     to filter by runtimes that have downloaded models in registry.

        Returns:
            List of available workers matching criteria, sorted by available capacity
        """
        workers = await self.get_workers()
        available = []

        for worker in workers:
            if not worker.is_available:
                continue

            # Model/Runtime filtering:
            # - model=None, runtime=None: any worker (or filter by valid_runtimes)
            # - model specified: prefer workers with model loaded, fallback to runtime match
            # - runtime specified: workers with matching runtime (can load any model)
            if model is not None:
                # First check if model is already loaded
                if model in worker.models_loaded:
                    pass  # Model loaded, worker matches
                elif runtime and worker.runtime == runtime:
                    pass  # Model not loaded but runtime matches, worker can load it
                else:
                    continue  # No match
            elif runtime is not None:
                # No specific model, but filter by runtime
                if worker.runtime != runtime:
                    continue
            elif valid_runtimes is not None:
                # No specific model or runtime, but filter by valid runtimes from registry
                # This ensures "Any available" only routes to workers whose runtime
                # has downloaded models in the registry
                if worker.runtime not in valid_runtimes:
                    continue

            # Language filtering
            if language != "auto" and "auto" not in worker.languages_supported:
                if language not in worker.languages_supported:
                    continue

            available.append(worker)

        # Sort: prefer workers with model loaded, then by available capacity
        if model:
            available.sort(
                key=lambda w: (model not in w.models_loaded, -w.available_capacity)
            )
        else:
            # Prefer workers that have models loaded (ready to go) over empty workers
            available.sort(
                key=lambda w: (len(w.models_loaded) == 0, -w.available_capacity)
            )

        return available

    async def mark_worker_offline(self, worker_id: str) -> None:
        """Mark worker as offline due to stale heartbeat.

        Args:
            worker_id: Worker identifier
        """
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        await self._redis.hset(worker_key, "status", "offline")
        logger.warning("worker_marked_offline", worker_id=worker_id)

    async def get_worker_session_ids(self, worker_id: str) -> set[str]:
        """Get active session IDs for a worker.

        Args:
            worker_id: Worker identifier

        Returns:
            Set of session IDs
        """
        sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"
        return await self._redis.smembers(sessions_key)

    def _parse_worker_state(self, worker_id: str, data: dict) -> WorkerState | None:
        """Parse worker state from Redis hash data.

        Returns None if critical fields are missing/invalid, which quarantines
        the worker from the pool until the issue is resolved.
        """
        # Critical fields - worker is unusable without these
        endpoint = data.get("endpoint")
        if not endpoint:
            logger.error(
                "worker_quarantined_missing_endpoint",
                worker_id=worker_id,
                raw_data=dict(data),
            )
            return None

        capacity_str = data.get("capacity")
        try:
            capacity = int(capacity_str) if capacity_str else 0
            if capacity <= 0:
                raise ValueError("capacity must be positive")
        except (ValueError, TypeError) as e:
            logger.error(
                "worker_quarantined_invalid_capacity",
                worker_id=worker_id,
                capacity_raw=capacity_str,
                error=str(e),
            )
            return None

        engine = data.get("engine")
        if not engine:
            logger.error(
                "worker_quarantined_missing_engine",
                worker_id=worker_id,
                raw_data=dict(data),
            )
            return None

        # Parse JSON fields with validation
        try:
            models_loaded = json.loads(data.get("models_loaded", "[]"))
            if not isinstance(models_loaded, list):
                raise ValueError("models_loaded must be a list")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "worker_invalid_models_loaded",
                worker_id=worker_id,
                raw_value=data.get("models_loaded"),
                error=str(e),
            )
            models_loaded = []

        try:
            languages_supported = json.loads(data.get("languages_supported", "[]"))
            if not isinstance(languages_supported, list):
                raise ValueError("languages_supported must be a list")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "worker_invalid_languages_supported",
                worker_id=worker_id,
                raw_value=data.get("languages_supported"),
                error=str(e),
            )
            languages_supported = []

        try:
            active_sessions = int(data.get("active_sessions", "0"))
        except (ValueError, TypeError):
            logger.warning(
                "worker_invalid_active_sessions",
                worker_id=worker_id,
                raw_value=data.get("active_sessions"),
            )
            active_sessions = 0

        return WorkerState(
            worker_id=worker_id,
            endpoint=endpoint,
            status=data.get("status", "offline"),
            capacity=capacity,
            active_sessions=active_sessions,
            models_loaded=models_loaded,
            languages_supported=languages_supported,
            engine=engine,
            runtime=data.get("runtime"),
            supports_vocabulary=data.get("supports_vocabulary", "false") == "true",
            gpu_memory_used=data.get("gpu_memory_used", "0GB"),
            gpu_memory_total=data.get("gpu_memory_total", "0GB"),
            last_heartbeat=self._parse_datetime(
                worker_id, "last_heartbeat", data.get("last_heartbeat")
            ),
            started_at=self._parse_datetime(
                worker_id, "started_at", data.get("started_at")
            ),
        )

    def _parse_datetime(
        self, worker_id: str, field: str, value: str | None
    ) -> datetime:
        """Parse ISO datetime string.

        Returns epoch (1970-01-01) on parse failure, which will cause the worker
        to be flagged as stale by health checks rather than appearing fresh.
        """
        if not value:
            logger.warning(
                "worker_missing_timestamp",
                worker_id=worker_id,
                field=field,
            )
            return datetime.min.replace(tzinfo=UTC)

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as e:
            logger.warning(
                "worker_invalid_timestamp",
                worker_id=worker_id,
                field=field,
                raw_value=value,
                error=str(e),
            )
            return datetime.min.replace(tzinfo=UTC)
