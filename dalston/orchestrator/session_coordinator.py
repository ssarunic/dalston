"""Session coordination for real-time transcription (M66).

Centralises realtime session lifecycle—allocation, keepalive, release,
orphan reconciliation, and offline detection—in the orchestrator package.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as redis
import structlog

from dalston.common.registry import EngineRecord, UnifiedEngineRegistry
from dalston.common.timeouts import REALTIME_SESSION_TTL_SECONDS
from dalston.orchestrator.session_allocator import (
    SessionAllocator,
    SessionState,
    WorkerAllocation,
)
from dalston.orchestrator.session_health import HealthMonitor

logger = structlog.get_logger()


@dataclass
class WorkerStatus:
    """Worker status for API and management responses.

    Maintains the gateway-facing response shape used by realtime status APIs.
    """

    instance: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models: list[str]
    languages: list[str]
    runtime: str | None = None
    supports_vocabulary: bool = False

    @classmethod
    def from_engine_record(cls, record: EngineRecord) -> WorkerStatus:
        """Create from EngineRecord."""
        return cls(
            instance=record.instance,
            endpoint=record.endpoint or "",
            status=record.status,
            capacity=record.capacity,
            active_sessions=record.active_realtime,
            models=record.models_loaded or [],
            languages=record.languages or [],
            runtime=record.runtime,
            supports_vocabulary=False,
        )


@dataclass
class CapacityInfo:
    """Capacity summary for API and management responses."""

    total_capacity: int
    used_capacity: int
    available_capacity: int
    worker_count: int
    ready_workers: int


class SessionCoordinator:
    """Orchestrator-side coordinator for real-time worker pool management.

    - Atomic capacity reservation + rollback on race.
    - Session TTL management (extend on activity).
    - Orphan reconciliation (crashed Gateway instances).
    - Offline worker detection + pub/sub fan-out.

    Usage::

        coordinator = SessionCoordinator(redis_url="redis://localhost:6379")
        await coordinator.start()

        allocation = await coordinator.acquire_worker(
            language="en",
            model=None,
            client_ip="10.0.0.1",
        )
        if allocation:
            ...  # proxy client to allocation.endpoint
        else:
            ...  # 503 – no capacity

        await coordinator.release_worker(allocation.session_id)
        await coordinator.stop()
    """

    def __init__(
        self,
        redis_url: str,
    ) -> None:
        """Initialise coordinator.

        Args:
            redis_url: Redis connection URL.
        """
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._registry: UnifiedEngineRegistry | None = None
        self._allocator: SessionAllocator | None = None
        self._health: HealthMonitor | None = None

    async def start(self) -> None:
        """Start the coordinator.

        Creates the Redis connection and starts background health monitoring.
        Call once at application startup.
        """
        self._redis = redis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        self._registry = UnifiedEngineRegistry(self._redis)
        self._allocator = SessionAllocator(self._redis, self._registry)
        self._health = HealthMonitor(self._redis, self._registry)
        await self._health.start()
        logger.info("session_coordinator_started")

    async def stop(self) -> None:
        """Stop the coordinator.

        Stops health monitoring and closes the Redis connection.
        Call once at application shutdown.
        """
        if self._health:
            await self._health.stop()
        if self._redis:
            await self._redis.close()
            self._redis = None
        self._registry = None
        self._allocator = None
        self._health = None
        logger.info("session_coordinator_stopped")

    # ------------------------------------------------------------------
    # Session allocation
    # ------------------------------------------------------------------

    async def acquire_worker(
        self,
        language: str,
        model: str | None,
        client_ip: str,
        runtime: str | None = None,
        valid_runtimes: set[str] | None = None,
    ) -> WorkerAllocation | None:
        """Acquire a worker for a new session.

        Selects the least-loaded available worker and atomically increments
        its active_sessions counter, rolling back on race-condition overflow.

        Args:
            language: Requested language code or ``"auto"``.
            model: Exact model name or *None* for any.
            client_ip: Client IP address (for logging and session record).
            runtime: Runtime hint for workers that can load arbitrary models.
            valid_runtimes: Restrict "any available" routing to this set.

        Returns:
            :class:`WorkerAllocation` on success, *None* if no capacity.
        """
        if not self._allocator:
            raise RuntimeError("SessionCoordinator not started")
        return await self._allocator.acquire_worker(
            language=language,
            model=model,
            client_ip=client_ip,
            runtime=runtime,
            valid_runtimes=valid_runtimes,
        )

    async def release_worker(self, session_id: str) -> SessionState | None:
        """Release a worker when a session ends.

        Decrements the worker's active_sessions counter and marks the
        session key as ended with a short TTL.

        Args:
            session_id: Session identifier returned by :meth:`acquire_worker`.

        Returns:
            :class:`SessionState` if found, *None* if session was unknown.
        """
        if not self._allocator:
            raise RuntimeError("SessionCoordinator not started")
        return await self._allocator.release_worker(session_id)

    async def get_session(self, session_id: str) -> SessionState | None:
        """Get current session state.

        Args:
            session_id: Session identifier.

        Returns:
            :class:`SessionState` if found, *None* otherwise.
        """
        if not self._allocator:
            raise RuntimeError("SessionCoordinator not started")
        return await self._allocator.get_session(session_id)

    async def extend_session_ttl(
        self,
        session_id: str,
        ttl: int = REALTIME_SESSION_TTL_SECONDS,
    ) -> None:
        """Extend session TTL to prevent expiration during long sessions.

        Call periodically for sessions that may exceed the default TTL.

        Args:
            session_id: Session identifier.
            ttl: New TTL in seconds (default: :data:`REALTIME_SESSION_TTL_SECONDS`).
        """
        if not self._allocator:
            raise RuntimeError("SessionCoordinator not started")
        await self._allocator.extend_session_ttl(session_id, ttl)

    # ------------------------------------------------------------------
    # Worker pool introspection
    # ------------------------------------------------------------------

    async def list_workers(self) -> list[WorkerStatus]:
        """List all registered workers with their current status.

        Returns:
            List of :class:`WorkerStatus` for every known worker.
        """
        if not self._registry:
            raise RuntimeError("SessionCoordinator not started")
        workers = [
            e
            for e in await self._registry.get_all()
            if e.supports_interface("realtime")
        ]
        return [WorkerStatus.from_engine_record(w) for w in workers]

    async def get_worker(self, instance: str) -> WorkerStatus | None:
        """Get status for a specific worker instance.

        Args:
            instance: Worker instance identifier.

        Returns:
            :class:`WorkerStatus` if found, *None* otherwise.
        """
        if not self._registry:
            raise RuntimeError("SessionCoordinator not started")
        worker = await self._registry.get_by_instance(instance)
        if worker:
            return WorkerStatus.from_engine_record(worker)
        return None

    async def get_capacity(self) -> CapacityInfo:
        """Get aggregate capacity statistics for the worker pool.

        Returns:
            :class:`CapacityInfo` with total, used, and available capacity.
        """
        if not self._registry:
            raise RuntimeError("SessionCoordinator not started")
        workers = [
            e
            for e in await self._registry.get_all()
            if e.supports_interface("realtime")
        ]
        total = sum(w.capacity for w in workers)
        used = sum(w.active_realtime for w in workers)
        ready = sum(1 for w in workers if w.status in ("ready", "busy"))
        return CapacityInfo(
            total_capacity=total,
            used_capacity=used,
            available_capacity=total - used,
            worker_count=len(workers),
            ready_workers=ready,
        )

    @property
    def is_running(self) -> bool:
        """Whether the coordinator is running."""
        return self._redis is not None
