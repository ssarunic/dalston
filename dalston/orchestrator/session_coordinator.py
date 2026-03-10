"""Session coordination for real-time transcription (M66).

Centralises realtime session lifecycle—allocation, keepalive, release,
orphan reconciliation, and offline detection—in the orchestrator package.

This module has strict behavioural parity with ``dalston.session_router``:
it shares the same Redis key schema so the two can run side-by-side during
the parity observation window (Phase 2).  Legacy removal (Phase 3) is
tracked separately.

Feature flags
-------------
``DALSTON_SESSION_COORDINATOR_ENABLED=true``
    Gateway uses ``SessionCoordinator`` for session allocation instead of the
    legacy ``SessionRouter``.

``DALSTON_SESSION_PARITY_MONITOR_ENABLED=true``
    Activates ``ParityMonitor``, a **read-only** background loop that samples
    Redis state and logs capacity drift between the coordinator path and the
    legacy registry.  It does **not** run health checks, mark workers offline,
    reconcile orphans, or publish events—those mutations are performed
    exclusively by the active coordinator.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import redis.asyncio as redis
import structlog

from dalston.common.timeouts import REALTIME_SESSION_TTL_SECONDS
from dalston.orchestrator.realtime_registry import (
    ACTIVE_SESSIONS_KEY,
    WorkerRegistry,
    WorkerState,
)
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

    Mirrors ``session_router.router.WorkerStatus`` exactly so the gateway
    can treat coordinator and legacy router interchangeably.
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
    def from_worker_state(cls, state: WorkerState) -> WorkerStatus:
        """Create from WorkerState."""
        return cls(
            instance=state.instance,
            endpoint=state.endpoint,
            status=state.status,
            capacity=state.capacity,
            active_sessions=state.active_sessions,
            models=state.models_loaded,
            languages=state.languages_supported,
            runtime=state.runtime,
            supports_vocabulary=state.supports_vocabulary,
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

    Behavioural parity with ``session_router.SessionRouter``:

    - Atomic capacity reservation + rollback on race.
    - Session TTL management (extend on activity).
    - Orphan reconciliation (crashed Gateway instances).
    - Offline worker detection + pub/sub fan-out.

    The coordinator reuses the session_router components (WorkerRegistry,
    SessionAllocator, HealthMonitor) which already share Redis key semantics
    with the legacy router.  This means both can run concurrently against
    the same Redis state during the parity window without conflicts.

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
        *,
        unified_read_enabled: bool | None = None,
    ) -> None:
        """Initialise coordinator.

        Args:
            redis_url: Redis connection URL.
            unified_read_enabled: When *True* the unified engine registry
                (M64) is preferred for worker discovery with legacy fallback.
                When *None* the value of
                ``DALSTON_REGISTRY_UNIFIED_READ_ENABLED`` is used.
        """
        self._redis_url = redis_url
        self._unified_read_enabled = unified_read_enabled
        self._redis: redis.Redis | None = None
        self._registry: WorkerRegistry | None = None
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
        self._registry = WorkerRegistry(
            self._redis,
            unified_read_enabled=self._unified_read_enabled,
        )
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
        workers = await self._registry.get_workers()
        return [WorkerStatus.from_worker_state(w) for w in workers]

    async def get_worker(self, instance: str) -> WorkerStatus | None:
        """Get status for a specific worker instance.

        Args:
            instance: Worker instance identifier.

        Returns:
            :class:`WorkerStatus` if found, *None* otherwise.
        """
        if not self._registry:
            raise RuntimeError("SessionCoordinator not started")
        worker = await self._registry.get_worker(instance)
        if worker:
            return WorkerStatus.from_worker_state(worker)
        return None

    async def get_capacity(self) -> CapacityInfo:
        """Get aggregate capacity statistics for the worker pool.

        Returns:
            :class:`CapacityInfo` with total, used, and available capacity.
        """
        if not self._registry:
            raise RuntimeError("SessionCoordinator not started")
        workers = await self._registry.get_workers()
        total = sum(w.capacity for w in workers)
        used = sum(w.active_sessions for w in workers)
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


class ParityMonitor:
    """Read-only observer that samples coordinator state for drift detection.

    Runs a background loop that periodically reads worker pool and session
    counts from Redis and logs any discrepancies.

    **Crucially non-mutating**: it never marks workers offline, reconciles
    orphan sessions, or publishes pub/sub events.  All mutations are the
    exclusive responsibility of the active :class:`SessionCoordinator`.

    This avoids the double-side-effect problem that would occur if a second
    full ``SessionRouter`` (with its own ``HealthMonitor``) ran alongside the
    coordinator against the same Redis keys.

    Usage::

        monitor = ParityMonitor(coordinator)
        await monitor.start()
        # ... run for a while ...
        await monitor.stop()
    """

    CHECK_INTERVAL = 30  # seconds between parity snapshots

    def __init__(self, coordinator: SessionCoordinator) -> None:
        """Initialise parity monitor.

        Args:
            coordinator: The active :class:`SessionCoordinator` whose Redis
                client and registry will be used for read-only sampling.
        """
        self._coordinator = coordinator
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background parity sampling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("session_parity_monitor_started")

    async def stop(self) -> None:
        """Stop the background parity sampling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("session_parity_monitor_stopped")

    async def _run_loop(self) -> None:
        """Periodic parity snapshot loop."""
        while self._running:
            try:
                await self._snapshot()
            except Exception as exc:
                logger.warning("parity_snapshot_error", error=str(exc))
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _snapshot(self) -> None:
        """Read current state and log capacity metrics for drift detection."""
        registry = self._coordinator._registry
        redis_client = self._coordinator._redis
        if registry is None or redis_client is None:
            return

        workers = await registry.get_workers()
        total = sum(w.capacity for w in workers)
        used = sum(w.active_sessions for w in workers)
        active_session_count = len(await redis_client.smembers(ACTIVE_SESSIONS_KEY))

        logger.info(
            "parity_snapshot",
            worker_count=len(workers),
            total_capacity=total,
            used_capacity=used,
            available_capacity=total - used,
            active_sessions_index=active_session_count,
            drift=used - active_session_count,
        )

    @property
    def is_running(self) -> bool:
        """Whether the parity monitor is running."""
        return self._running
