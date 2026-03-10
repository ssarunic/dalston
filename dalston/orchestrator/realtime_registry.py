"""Server-side worker registry for realtime transcription (moved from session_router, M66).

Reads worker state from Redis (written by realtime_sdk's WorkerRegistry client).

When DALSTON_REGISTRY_UNIFIED_READ_ENABLED=true, reads from the unified
registry first with legacy fallback for RT worker discovery (M64).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

from dalston.common.registry import EngineRecord, UnifiedEngineRegistry

logger = structlog.get_logger()


# Redis key patterns (shared with realtime_sdk)
INSTANCE_SET_KEY = "dalston:realtime:instances"
INSTANCE_KEY_PREFIX = "dalston:realtime:instance:"
INSTANCE_SESSIONS_SUFFIX = ":sessions"
SESSION_KEY_PREFIX = "dalston:realtime:session:"
ACTIVE_SESSIONS_KEY = "dalston:realtime:sessions:active"
EVENTS_CHANNEL = "dalston:realtime:events"


@dataclass
class WorkerState:
    """Worker state read from Redis.

    Attributes:
        instance: Unique instance identifier
        endpoint: WebSocket endpoint URL
        status: Current status (ready, busy, draining, offline)
        capacity: Maximum concurrent sessions
        active_sessions: Current active session count
        models_loaded: List of currently loaded model variants
        languages_supported: List of supported language codes
        runtime: The inference framework (e.g., "faster-whisper", "parakeet")
        supports_vocabulary: Whether this engine supports vocabulary boosting
        gpu_memory_used: GPU memory usage string
        gpu_memory_total: Total GPU memory string
        last_heartbeat: Last heartbeat timestamp
        started_at: Worker start timestamp
    """

    instance: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models_loaded: list[str]
    languages_supported: list[str]
    runtime: str
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

    Used by SessionCoordinator to:
    - List all registered workers
    - Find workers with available capacity
    - Check worker health status
    - Mark workers offline

    Example::

        registry = WorkerRegistry(redis_client)

        # Get all workers
        workers = await registry.get_workers()

        # Find available workers for a request
        available = await registry.get_available_workers(
            model=None,
            language="en",
        )

        # Mark stale worker offline
        await registry.mark_worker_offline("stt-rt-transcribe-whisper-1")
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        *,
        unified_read_enabled: bool | None = None,
    ) -> None:
        """Initialize registry with Redis client.

        Args:
            redis_client: Async Redis client.
            unified_read_enabled: Read from unified registry first (M64).
                If None, reads from ``DALSTON_REGISTRY_UNIFIED_READ_ENABLED`` env var.
        """
        self._redis = redis_client

        if unified_read_enabled is None:
            import os

            unified_read_enabled = (
                os.environ.get("DALSTON_REGISTRY_UNIFIED_READ_ENABLED", "false").lower()
                == "true"
            )
        self._unified_read_enabled = unified_read_enabled
        self._unified = (
            UnifiedEngineRegistry(redis_client) if unified_read_enabled else None
        )

    async def get_workers(self) -> list[WorkerState]:
        """Get all registered workers."""
        if self._unified:
            try:
                records = await self._unified.get_all()
                rt_records = [r for r in records if r.supports_interface("realtime")]
                if rt_records:
                    return [_engine_record_to_worker_state(r) for r in rt_records]
            except Exception:
                logger.debug("unified_registry_read_fallback", method="get_workers")

        instances = await self._redis.smembers(INSTANCE_SET_KEY)
        workers = []
        for instance in instances:
            worker = await self.get_worker(instance)
            if worker is not None:
                workers.append(worker)
        return workers

    async def get_worker(self, instance: str) -> WorkerState | None:
        """Get specific worker state."""
        if self._unified:
            try:
                record = await self._unified.get_by_instance(instance)
                if record is not None and record.supports_interface("realtime"):
                    return _engine_record_to_worker_state(record)
            except Exception:
                logger.debug("unified_registry_read_fallback", method="get_worker")

        worker_key = f"{INSTANCE_KEY_PREFIX}{instance}"
        data = await self._redis.hgetall(worker_key)
        if not data:
            return None
        return self._parse_worker_state(instance, data)

    async def get_available_workers(
        self,
        model: str | None,
        language: str,
        runtime: str | None = None,
        valid_runtimes: set[str] | None = None,
    ) -> list[WorkerState]:
        """Get workers with capacity matching the requested model/language/runtime."""
        if self._unified:
            try:
                records = await self._unified.get_available(
                    interface="realtime",
                    language=language,
                    model=model,
                    runtime=runtime,
                    valid_runtimes=valid_runtimes,
                )
                if records:
                    return [_engine_record_to_worker_state(r) for r in records]
            except Exception:
                logger.debug(
                    "unified_registry_read_fallback",
                    method="get_available_workers",
                )

        workers = await self.get_workers()
        available = []

        for worker in workers:
            if not worker.is_available:
                continue

            if model is not None:
                if model in worker.models_loaded:
                    pass
                elif runtime and worker.runtime == runtime:
                    pass
                else:
                    continue
            elif runtime is not None:
                if worker.runtime != runtime:
                    continue
            elif valid_runtimes is not None:
                if worker.runtime not in valid_runtimes:
                    continue

            if language != "auto" and "auto" not in worker.languages_supported:
                if language not in worker.languages_supported:
                    continue

            available.append(worker)

        if model:
            available.sort(
                key=lambda w: (model not in w.models_loaded, -w.available_capacity)
            )
        else:
            available.sort(
                key=lambda w: (len(w.models_loaded) == 0, -w.available_capacity)
            )

        return available

    async def mark_worker_offline(self, instance: str) -> None:
        """Mark worker as offline due to stale heartbeat."""
        worker_key = f"{INSTANCE_KEY_PREFIX}{instance}"
        await self._redis.hset(worker_key, "status", "offline")

        if self._unified:
            try:
                await self._unified.mark_instance_offline(instance)
            except Exception:
                logger.debug("unified_registry_mark_offline_failed", instance=instance)

        logger.warning("worker_marked_offline", instance=instance)

    async def get_worker_session_ids(self, instance: str) -> set[str]:
        """Get active session IDs for a worker."""
        sessions_key = f"{INSTANCE_KEY_PREFIX}{instance}{INSTANCE_SESSIONS_SUFFIX}"
        return await self._redis.smembers(sessions_key)

    def _parse_worker_state(self, instance: str, data: dict) -> WorkerState | None:
        """Parse worker state from Redis hash data.

        Returns None if critical fields are missing/invalid, quarantining
        the worker from the pool until the issue is resolved.
        """
        endpoint = data.get("endpoint")
        if not endpoint:
            logger.error(
                "worker_quarantined_missing_endpoint",
                instance=instance,
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
                instance=instance,
                capacity_raw=capacity_str,
                error=str(e),
            )
            return None

        runtime = data.get("runtime")
        if not runtime:
            logger.error(
                "worker_quarantined_missing_runtime",
                instance=instance,
                raw_data=dict(data),
            )
            return None

        try:
            models_loaded = json.loads(data.get("models_loaded", "[]"))
            if not isinstance(models_loaded, list):
                raise ValueError("models_loaded must be a list")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "worker_invalid_models_loaded",
                instance=instance,
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
                instance=instance,
                raw_value=data.get("languages_supported"),
                error=str(e),
            )
            languages_supported = []

        try:
            active_sessions = int(data.get("active_sessions", "0"))
        except (ValueError, TypeError):
            logger.warning(
                "worker_invalid_active_sessions",
                instance=instance,
                raw_value=data.get("active_sessions"),
            )
            active_sessions = 0

        return WorkerState(
            instance=instance,
            endpoint=endpoint,
            status=data.get("status", "offline"),
            capacity=capacity,
            active_sessions=active_sessions,
            models_loaded=models_loaded,
            languages_supported=languages_supported,
            runtime=runtime,
            supports_vocabulary=data.get("supports_vocabulary", "false") == "true",
            gpu_memory_used=data.get("gpu_memory_used", "0GB"),
            gpu_memory_total=data.get("gpu_memory_total", "0GB"),
            last_heartbeat=self._parse_datetime(
                instance, "last_heartbeat", data.get("last_heartbeat")
            ),
            started_at=self._parse_datetime(
                instance, "started_at", data.get("started_at")
            ),
        )

    def _parse_datetime(self, instance: str, field: str, value: str | None) -> datetime:
        """Parse ISO datetime string, returning epoch on failure."""
        if not value:
            logger.warning("worker_missing_timestamp", instance=instance, field=field)
            return datetime.min.replace(tzinfo=UTC)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as e:
            logger.warning(
                "worker_invalid_timestamp",
                instance=instance,
                field=field,
                raw_value=value,
                error=str(e),
            )
            return datetime.min.replace(tzinfo=UTC)


def _engine_record_to_worker_state(record: EngineRecord) -> WorkerState:
    """Convert unified EngineRecord to WorkerState (M64 bridge)."""
    return WorkerState(
        instance=record.instance,
        endpoint=record.endpoint or "",
        status=record.status,
        capacity=record.capacity,
        active_sessions=record.active_realtime,
        models_loaded=record.models_loaded or [],
        languages_supported=record.languages or [],
        runtime=record.runtime,
        supports_vocabulary=False,
        gpu_memory_used=record.gpu_memory_used,
        gpu_memory_total=record.gpu_memory_total,
        last_heartbeat=record.last_heartbeat or datetime.now(UTC),
        started_at=record.registered_at or datetime.now(UTC),
    )
