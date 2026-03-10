"""Session allocation for real-time transcription (moved from session_router, M66).

Implements least-loaded allocation strategy for distributing
sessions across available workers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

import dalston.metrics
import dalston.telemetry
from dalston.common.registry import UnifiedEngineRegistry
from dalston.common.timeouts import REALTIME_SESSION_TTL_SECONDS
from dalston.orchestrator.realtime_registry import (
    ACTIVE_SESSIONS_KEY,
    INSTANCE_KEY_PREFIX,
    INSTANCE_SESSIONS_SUFFIX,
    INSTANCE_SET_KEY,
    SESSION_KEY_PREFIX,
)

logger = structlog.get_logger()


@dataclass
class WorkerAllocation:
    """Result of successful worker allocation.

    Attributes:
        instance: Allocated instance identifier
        endpoint: Worker WebSocket endpoint URL
        session_id: Newly created session ID
        runtime: Runtime framework (e.g., "faster-whisper", "parakeet")
    """

    instance: str
    endpoint: str
    session_id: str
    runtime: str


@dataclass
class SessionState:
    """Session state stored in Redis.

    Attributes:
        session_id: Session identifier
        instance: Assigned instance
        status: Session status (active, ended, error)
        language: Requested language
        model: Requested model variant
        client_ip: Client IP address
        started_at: Session start timestamp
    """

    session_id: str
    instance: str
    status: str
    language: str
    model: str
    client_ip: str
    started_at: datetime


class SessionAllocator:
    """Allocates sessions to workers using least-loaded strategy.

    Handles:
    - Finding workers with available capacity
    - Atomic capacity reservation
    - Session state management
    - Capacity release on session end

    Example:
        allocator = SessionAllocator(redis_client, registry)

        # Acquire worker for new session
        allocation = await allocator.acquire_worker(
            language="en",
            model=None,  # None = auto, or specific model name
            client_ip="192.168.1.100"
        )

        if allocation:
            # Connect client to worker endpoint
            connect_to(allocation.endpoint)
        else:
            # No capacity available
            return error("no_capacity")

        # Release on session end
        await allocator.release_worker(allocation.session_id)
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        registry: UnifiedEngineRegistry,
    ) -> None:
        """Initialize allocator.

        Args:
            redis_client: Async Redis client
            registry: Engine registry for reading worker state
        """
        self._redis = redis_client
        self._registry = registry

    async def acquire_worker(
        self,
        language: str,
        model: str | None,
        client_ip: str,
        runtime: str | None = None,
        valid_runtimes: set[str] | None = None,
    ) -> WorkerAllocation | None:
        """Find worker with capacity and reserve a slot.

        Uses least-loaded strategy: selects worker with most available capacity.

        Args:
            language: Requested language code or "auto"
            model: Model name (e.g., "faster-whisper-large-v3") or None for any
            client_ip: Client IP address for logging
            runtime: Model runtime (e.g., "faster-whisper") for routing when model
                     isn't pre-loaded. Workers matching runtime can load the model.
            valid_runtimes: When model=None and runtime=None, only consider workers
                     whose runtime is in this set. Used for "Any available" routing.

        Returns:
            WorkerAllocation if successful, None if no capacity available
        """
        allocation_start = time.perf_counter()
        with dalston.telemetry.create_span(
            "session_router.allocate",
            attributes={
                "dalston.language": language,
                "dalston.model": model,
                "dalston.runtime": runtime,
            },
        ):
            # Find available workers
            available = await self._registry.get_available(
                interface="realtime",
                language=language,
                runtime=runtime,
                model=model,
                valid_runtimes=valid_runtimes,
            )

            if not available:
                logger.warning("no_workers_available", model=model, language=language)
                return None

            # Select best worker (first in list = most available capacity)
            worker = available[0]

            # Generate session ID
            session_id = f"sess_{uuid.uuid4().hex[:16]}"

            # Atomically increment active_sessions
            instance_key = f"{INSTANCE_KEY_PREFIX}{worker.instance}"
            new_count = await self._redis.hincrby(instance_key, "active_sessions", 1)

            # Verify we didn't exceed capacity (race condition check)
            if new_count > worker.capacity:
                # Rollback
                await self._redis.hincrby(instance_key, "active_sessions", -1)
                logger.warning(
                    "instance_at_capacity_rollback", instance=worker.instance
                )
                # Try next worker
                if len(available) > 1:
                    return await self._acquire_from_list(
                        available[1:], language, model, client_ip
                    )
                return None

            # Create session record
            await self._create_session(
                session_id=session_id,
                instance=worker.instance,
                language=language,
                model=model,
                client_ip=client_ip,
            )

            # Add to instance's session set and instance index
            sessions_key = (
                f"{INSTANCE_KEY_PREFIX}{worker.instance}{INSTANCE_SESSIONS_SUFFIX}"
            )
            await self._redis.sadd(sessions_key, session_id)
            await self._redis.sadd(INSTANCE_SET_KEY, worker.instance)

            # Add to active sessions index
            await self._redis.sadd(ACTIVE_SESSIONS_KEY, session_id)

            # Set span attributes for allocated session
            dalston.telemetry.set_span_attribute("dalston.session_id", session_id)
            dalston.telemetry.set_span_attribute("dalston.instance", worker.instance)

            # Record allocation duration metric (M20)
            dalston.metrics.observe_session_router_allocation(
                time.perf_counter() - allocation_start
            )
            # Update active sessions gauge for this instance
            dalston.metrics.set_session_router_sessions_active(
                worker.instance, new_count
            )

            logger.info(
                "session_allocated",
                session_id=session_id,
                instance=worker.instance,
                active=new_count,
                capacity=worker.capacity,
            )

            return WorkerAllocation(
                instance=worker.instance,
                endpoint=worker.endpoint,
                session_id=session_id,
                runtime=worker.runtime,
            )

    async def _acquire_from_list(
        self,
        workers: list,
        language: str,
        model: str | None,
        client_ip: str,
    ) -> WorkerAllocation | None:
        """Try to acquire from remaining workers in list."""
        for worker in workers:
            session_id = f"sess_{uuid.uuid4().hex[:16]}"
            instance_key = f"{INSTANCE_KEY_PREFIX}{worker.instance}"
            new_count = await self._redis.hincrby(instance_key, "active_sessions", 1)

            if new_count <= worker.capacity:
                await self._create_session(
                    session_id=session_id,
                    instance=worker.instance,
                    language=language,
                    model=model,
                    client_ip=client_ip,
                )

                sessions_key = (
                    f"{INSTANCE_KEY_PREFIX}{worker.instance}{INSTANCE_SESSIONS_SUFFIX}"
                )
                await self._redis.sadd(sessions_key, session_id)
                await self._redis.sadd(INSTANCE_SET_KEY, worker.instance)
                await self._redis.sadd(ACTIVE_SESSIONS_KEY, session_id)

                logger.info(
                    "session_allocated",
                    session_id=session_id,
                    instance=worker.instance,
                )

                return WorkerAllocation(
                    instance=worker.instance,
                    endpoint=worker.endpoint,
                    session_id=session_id,
                    runtime=worker.runtime,
                )

            # Rollback and try next
            await self._redis.hincrby(instance_key, "active_sessions", -1)

        return None

    async def _create_session(
        self,
        session_id: str,
        instance: str,
        language: str,
        model: str | None,
        client_ip: str,
    ) -> None:
        """Create session record in Redis."""
        session_key = f"{SESSION_KEY_PREFIX}{session_id}"

        await self._redis.hset(
            session_key,
            mapping={
                "instance": instance,
                "status": "active",
                "language": language,
                "model": model or "",  # Redis can't store None
                "client_ip": client_ip,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )

        # Set TTL for session cleanup (extended on activity)
        await self._redis.expire(session_key, REALTIME_SESSION_TTL_SECONDS)

    async def release_worker(self, session_id: str) -> SessionState | None:
        """Release capacity when session ends.

        Args:
            session_id: Session identifier

        Returns:
            SessionState if found, None otherwise
        """
        session_key = f"{SESSION_KEY_PREFIX}{session_id}"

        # Get session data
        data = await self._redis.hgetall(session_key)
        if not data:
            logger.warning("session_not_found", session_id=session_id)
            return None

        instance = data.get("instance")
        if not instance:
            logger.warning("session_no_instance", session_id=session_id)
            return None

        # Decrement instance's active sessions
        instance_key = f"{INSTANCE_KEY_PREFIX}{instance}"
        new_count = await self._redis.hincrby(instance_key, "active_sessions", -1)

        # Update active sessions gauge for this instance (M20)
        dalston.metrics.set_session_router_sessions_active(instance, max(0, new_count))

        # Remove from instance's session set
        sessions_key = f"{INSTANCE_KEY_PREFIX}{instance}{INSTANCE_SESSIONS_SUFFIX}"
        await self._redis.srem(sessions_key, session_id)

        # Remove from active sessions index
        await self._redis.srem(ACTIVE_SESSIONS_KEY, session_id)

        # Update session status
        await self._redis.hset(session_key, "status", "ended")

        # Set short TTL for cleanup
        await self._redis.expire(session_key, 60)  # 1 minute

        logger.info("session_released", session_id=session_id, instance=instance)

        return SessionState(
            session_id=session_id,
            instance=instance,
            status="ended",
            language=data.get("language", "auto"),
            model=data.get("model", "fast"),
            client_ip=data.get("client_ip", ""),
            started_at=self._parse_datetime(data.get("started_at")),
        )

    async def get_session(self, session_id: str) -> SessionState | None:
        """Get session state.

        Args:
            session_id: Session identifier

        Returns:
            SessionState if found, None otherwise
        """
        session_key = f"{SESSION_KEY_PREFIX}{session_id}"
        data = await self._redis.hgetall(session_key)

        if not data:
            return None

        return SessionState(
            session_id=session_id,
            instance=data.get("instance", ""),
            status=data.get("status", "unknown"),
            language=data.get("language", "auto"),
            model=data.get("model", "fast"),
            client_ip=data.get("client_ip", ""),
            started_at=self._parse_datetime(data.get("started_at")),
        )

    async def extend_session_ttl(
        self, session_id: str, ttl: int = REALTIME_SESSION_TTL_SECONDS
    ) -> None:
        """Extend session TTL (call periodically for long sessions).

        Args:
            session_id: Session identifier
            ttl: New TTL in seconds
        """
        session_key = f"{SESSION_KEY_PREFIX}{session_id}"
        await self._redis.expire(session_key, ttl)

    def _parse_datetime(self, value: str | None) -> datetime:
        """Parse ISO datetime string."""
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)
