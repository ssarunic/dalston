"""Session allocation for real-time transcription.

Implements least-loaded allocation strategy for distributing
sessions across available workers.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

from dalston.session_router.registry import (
    ACTIVE_SESSIONS_KEY,
    SESSION_KEY_PREFIX,
    WORKER_KEY_PREFIX,
    WORKER_SESSIONS_SUFFIX,
    WorkerRegistry,
)

logger = structlog.get_logger()


@dataclass
class WorkerAllocation:
    """Result of successful worker allocation.

    Attributes:
        worker_id: Allocated worker identifier
        endpoint: Worker WebSocket endpoint URL
        session_id: Newly created session ID
    """

    worker_id: str
    endpoint: str
    session_id: str


@dataclass
class SessionState:
    """Session state stored in Redis.

    Attributes:
        session_id: Session identifier
        worker_id: Assigned worker
        status: Session status (active, ended, error)
        language: Requested language
        model: Requested model variant
        client_ip: Client IP address
        started_at: Session start timestamp
        enhance_on_end: Whether to trigger batch enhancement
    """

    session_id: str
    worker_id: str
    status: str
    language: str
    model: str
    client_ip: str
    started_at: datetime
    enhance_on_end: bool = False


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
            model="fast",
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
        registry: WorkerRegistry,
    ) -> None:
        """Initialize allocator.

        Args:
            redis_client: Async Redis client
            registry: Worker registry for reading worker state
        """
        self._redis = redis_client
        self._registry = registry

    async def acquire_worker(
        self,
        language: str,
        model: str,
        client_ip: str,
        enhance_on_end: bool = False,
    ) -> WorkerAllocation | None:
        """Find worker with capacity and reserve a slot.

        Uses least-loaded strategy: selects worker with most available capacity.

        Args:
            language: Requested language code or "auto"
            model: Requested model variant ("fast" or "accurate")
            client_ip: Client IP address for logging
            enhance_on_end: Whether to trigger batch enhancement on session end

        Returns:
            WorkerAllocation if successful, None if no capacity available
        """
        # Find available workers
        available = await self._registry.get_available_workers(model, language)

        if not available:
            logger.warning("no_workers_available", model=model, language=language)
            return None

        # Select best worker (first in list = most available capacity)
        worker = available[0]

        # Generate session ID
        session_id = f"sess_{uuid.uuid4().hex[:16]}"

        # Atomically increment active_sessions
        worker_key = f"{WORKER_KEY_PREFIX}{worker.worker_id}"
        new_count = await self._redis.hincrby(worker_key, "active_sessions", 1)

        # Verify we didn't exceed capacity (race condition check)
        if new_count > worker.capacity:
            # Rollback
            await self._redis.hincrby(worker_key, "active_sessions", -1)
            logger.warning("worker_at_capacity_rollback", worker_id=worker.worker_id)
            # Try next worker
            if len(available) > 1:
                return await self._acquire_from_list(
                    available[1:], language, model, client_ip, enhance_on_end
                )
            return None

        # Create session record
        await self._create_session(
            session_id=session_id,
            worker_id=worker.worker_id,
            language=language,
            model=model,
            client_ip=client_ip,
            enhance_on_end=enhance_on_end,
        )

        # Add to worker's session set
        sessions_key = f"{WORKER_KEY_PREFIX}{worker.worker_id}{WORKER_SESSIONS_SUFFIX}"
        await self._redis.sadd(sessions_key, session_id)

        # Add to active sessions index
        await self._redis.sadd(ACTIVE_SESSIONS_KEY, session_id)

        logger.info(
            "session_allocated",
            session_id=session_id,
            worker_id=worker.worker_id,
            active=new_count,
            capacity=worker.capacity,
        )

        return WorkerAllocation(
            worker_id=worker.worker_id,
            endpoint=worker.endpoint,
            session_id=session_id,
        )

    async def _acquire_from_list(
        self,
        workers: list,
        language: str,
        model: str,
        client_ip: str,
        enhance_on_end: bool,
    ) -> WorkerAllocation | None:
        """Try to acquire from remaining workers in list."""
        for worker in workers:
            session_id = f"sess_{uuid.uuid4().hex[:16]}"
            worker_key = f"{WORKER_KEY_PREFIX}{worker.worker_id}"
            new_count = await self._redis.hincrby(worker_key, "active_sessions", 1)

            if new_count <= worker.capacity:
                await self._create_session(
                    session_id=session_id,
                    worker_id=worker.worker_id,
                    language=language,
                    model=model,
                    client_ip=client_ip,
                    enhance_on_end=enhance_on_end,
                )

                sessions_key = (
                    f"{WORKER_KEY_PREFIX}{worker.worker_id}{WORKER_SESSIONS_SUFFIX}"
                )
                await self._redis.sadd(sessions_key, session_id)
                await self._redis.sadd(ACTIVE_SESSIONS_KEY, session_id)

                logger.info(
                    "session_allocated",
                    session_id=session_id,
                    worker_id=worker.worker_id,
                )

                return WorkerAllocation(
                    worker_id=worker.worker_id,
                    endpoint=worker.endpoint,
                    session_id=session_id,
                )

            # Rollback and try next
            await self._redis.hincrby(worker_key, "active_sessions", -1)

        return None

    async def _create_session(
        self,
        session_id: str,
        worker_id: str,
        language: str,
        model: str,
        client_ip: str,
        enhance_on_end: bool,
    ) -> None:
        """Create session record in Redis."""
        session_key = f"{SESSION_KEY_PREFIX}{session_id}"

        await self._redis.hset(
            session_key,
            mapping={
                "worker_id": worker_id,
                "status": "active",
                "language": language,
                "model": model,
                "client_ip": client_ip,
                "started_at": datetime.now(UTC).isoformat(),
                "enhance_on_end": json.dumps(enhance_on_end),
            },
        )

        # Set TTL for session cleanup (extended on activity)
        await self._redis.expire(session_key, 300)  # 5 minutes

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

        worker_id = data.get("worker_id")
        if not worker_id:
            logger.warning("session_no_worker_id", session_id=session_id)
            return None

        # Decrement worker's active sessions
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        await self._redis.hincrby(worker_key, "active_sessions", -1)

        # Remove from worker's session set
        sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"
        await self._redis.srem(sessions_key, session_id)

        # Remove from active sessions index
        await self._redis.srem(ACTIVE_SESSIONS_KEY, session_id)

        # Update session status
        await self._redis.hset(session_key, "status", "ended")

        # Set short TTL for cleanup
        await self._redis.expire(session_key, 60)  # 1 minute

        logger.info("session_released", session_id=session_id, worker_id=worker_id)

        return SessionState(
            session_id=session_id,
            worker_id=worker_id,
            status="ended",
            language=data.get("language", "auto"),
            model=data.get("model", "fast"),
            client_ip=data.get("client_ip", ""),
            started_at=self._parse_datetime(data.get("started_at")),
            enhance_on_end=json.loads(data.get("enhance_on_end", "false")),
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
            worker_id=data.get("worker_id", ""),
            status=data.get("status", "unknown"),
            language=data.get("language", "auto"),
            model=data.get("model", "fast"),
            client_ip=data.get("client_ip", ""),
            started_at=self._parse_datetime(data.get("started_at")),
            enhance_on_end=json.loads(data.get("enhance_on_end", "false")),
        )

    async def extend_session_ttl(self, session_id: str, ttl: int = 300) -> None:
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
