"""Main Session Router for real-time transcription.

Coordinates worker pool management, session allocation, and health monitoring.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as redis
import structlog

from dalston.session_router.allocator import (
    SessionAllocator,
    SessionState,
    WorkerAllocation,
)
from dalston.session_router.health import HealthMonitor
from dalston.session_router.registry import WorkerRegistry, WorkerState

logger = structlog.get_logger()


@dataclass
class WorkerStatus:
    """Worker status for API responses.

    Simplified view of worker state for management APIs.
    """

    worker_id: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models: list[str]
    languages: list[str]
    engine: str = "unknown"

    @classmethod
    def from_worker_state(cls, state: WorkerState) -> WorkerStatus:
        """Create from WorkerState."""
        return cls(
            worker_id=state.worker_id,
            endpoint=state.endpoint,
            status=state.status,
            capacity=state.capacity,
            active_sessions=state.active_sessions,
            models=state.models_loaded,
            languages=state.languages_supported,
            engine=state.engine,
        )


@dataclass
class CapacityInfo:
    """Capacity information for API responses."""

    total_capacity: int
    used_capacity: int
    available_capacity: int
    worker_count: int
    ready_workers: int


class SessionRouter:
    """Main session router coordinating real-time worker pool.

    This is the primary interface used by Gateway for:
    - Acquiring workers for new sessions
    - Releasing workers when sessions end
    - Monitoring worker pool status

    The router manages:
    - WorkerRegistry: Reading worker state from Redis
    - SessionAllocator: Allocating sessions to workers
    - HealthMonitor: Checking worker health via heartbeat

    Example:
        router = SessionRouter(redis_url="redis://localhost:6379")

        # Start background tasks (health monitoring)
        await router.start()

        # Acquire worker for incoming WebSocket connection
        allocation = await router.acquire_worker(
            language="en",
            model=None,  # None = auto, or specific model name
            client_ip="192.168.1.100"
        )

        if allocation:
            # Proxy client to worker.endpoint
            pass
        else:
            # Return no_capacity error
            pass

        # Release on disconnect
        await router.release_worker(session_id)

        # Shutdown
        await router.stop()
    """

    def __init__(self, redis_url: str) -> None:
        """Initialize session router.

        Args:
            redis_url: Redis connection URL
        """
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._registry: WorkerRegistry | None = None
        self._allocator: SessionAllocator | None = None
        self._health: HealthMonitor | None = None

    async def start(self) -> None:
        """Start the session router.

        Initializes Redis connection and starts health monitoring.
        Call this on application startup.
        """
        # Create Redis connection
        self._redis = redis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

        # Initialize components
        self._registry = WorkerRegistry(self._redis)
        self._allocator = SessionAllocator(self._redis, self._registry)
        self._health = HealthMonitor(self._redis, self._registry)

        # Start health monitoring
        await self._health.start()

        logger.info("session_router_started")

    async def stop(self) -> None:
        """Stop the session router.

        Stops health monitoring and closes Redis connection.
        Call this on application shutdown.
        """
        if self._health:
            await self._health.stop()

        if self._redis:
            await self._redis.close()
            self._redis = None

        self._registry = None
        self._allocator = None
        self._health = None

        logger.info("session_router_stopped")

    async def acquire_worker(
        self,
        language: str,
        model: str | None,
        client_ip: str,
        enhance_on_end: bool = True,
    ) -> WorkerAllocation | None:
        """Acquire a worker for a new session.

        Finds a worker with available capacity and reserves a slot.

        Args:
            language: Requested language code or "auto"
            model: Model name (e.g., "faster-whisper-large-v3") or None for any
            client_ip: Client IP address for logging
            enhance_on_end: Whether to trigger batch enhancement on session end

        Returns:
            WorkerAllocation with endpoint and session_id, or None if no capacity
        """
        if not self._allocator:
            raise RuntimeError("Session router not started")

        return await self._allocator.acquire_worker(
            language=language,
            model=model,
            client_ip=client_ip,
            enhance_on_end=enhance_on_end,
        )

    async def release_worker(self, session_id: str) -> SessionState | None:
        """Release a worker when session ends.

        Decrements worker capacity and cleans up session state.

        Args:
            session_id: Session identifier

        Returns:
            SessionState if found, None otherwise
        """
        if not self._allocator:
            raise RuntimeError("Session router not started")

        return await self._allocator.release_worker(session_id)

    async def get_session(self, session_id: str) -> SessionState | None:
        """Get session state.

        Args:
            session_id: Session identifier

        Returns:
            SessionState if found, None otherwise
        """
        if not self._allocator:
            raise RuntimeError("Session router not started")

        return await self._allocator.get_session(session_id)

    async def list_workers(self) -> list[WorkerStatus]:
        """List all workers with status.

        Returns:
            List of WorkerStatus for all registered workers
        """
        if not self._registry:
            raise RuntimeError("Session router not started")

        workers = await self._registry.get_workers()
        return [WorkerStatus.from_worker_state(w) for w in workers]

    async def get_worker(self, worker_id: str) -> WorkerStatus | None:
        """Get specific worker status.

        Args:
            worker_id: Worker identifier

        Returns:
            WorkerStatus if found, None otherwise
        """
        if not self._registry:
            raise RuntimeError("Session router not started")

        worker = await self._registry.get_worker(worker_id)
        if worker:
            return WorkerStatus.from_worker_state(worker)
        return None

    async def get_capacity(self) -> CapacityInfo:
        """Get total and available capacity.

        Returns:
            CapacityInfo with capacity statistics
        """
        if not self._registry:
            raise RuntimeError("Session router not started")

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
        """Whether the router is running."""
        return self._redis is not None
