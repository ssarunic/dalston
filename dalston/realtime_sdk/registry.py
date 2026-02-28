"""Worker registry client for real-time engines.

Handles worker registration, heartbeat, and session notifications
to the Session Router via Redis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

logger = structlog.get_logger()


# Redis key patterns (shared with session_router)
WORKER_SET_KEY = "dalston:realtime:workers"
WORKER_KEY_PREFIX = "dalston:realtime:worker:"
WORKER_SESSIONS_SUFFIX = ":sessions"
SESSION_KEY_PREFIX = "dalston:realtime:session:"
EVENTS_CHANNEL = "dalston:realtime:events"


@dataclass
class WorkerInfo:
    """Worker registration information.

    Attributes:
        worker_id: Unique identifier for this worker
        endpoint: WebSocket endpoint URL (e.g., "ws://localhost:9000")
        capacity: Maximum concurrent sessions this worker can handle
        models: List of model names this worker supports (e.g., ["faster-whisper-large-v3"])
        languages: List of language codes supported (e.g., ["en", "es", "auto"])
        engine: Engine type identifier (e.g., "parakeet", "whisper")
        supports_vocabulary: Whether this engine supports vocabulary boosting
    """

    worker_id: str
    endpoint: str
    capacity: int
    models: list[str]
    languages: list[str]
    engine: str = "unknown"
    supports_vocabulary: bool = False


class WorkerRegistry:
    """Client for registering real-time workers with Session Router.

    Handles:
    - Worker registration on startup
    - Periodic heartbeat updates
    - Session start/end notifications
    - Worker unregistration on shutdown

    Example:
        registry = WorkerRegistry("redis://localhost:6379")

        # Register on startup
        await registry.register(WorkerInfo(
            worker_id="stt-rt-transcribe-whisper-1",
            endpoint="ws://localhost:9000",
            capacity=4,
            models=["faster-whisper-large-v3"],
            languages=["en", "auto"]
        ))

        # Send heartbeats periodically
        await registry.heartbeat(
            worker_id="stt-rt-transcribe-whisper-1",
            active_sessions=2,
            gpu_memory_used="4.2GB"
        )

        # Notify session events
        await registry.session_started("stt-rt-transcribe-whisper-1", "sess_abc123")
        await registry.session_ended("stt-rt-transcribe-whisper-1", "sess_abc123", 45.6, "completed")

        # Unregister on shutdown
        await registry.unregister("stt-rt-transcribe-whisper-1")
        await registry.close()
    """

    def __init__(self, redis_url: str) -> None:
        """Initialize registry client.

        Args:
            redis_url: Redis connection URL
        """
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        # Store registration info for re-registration if key is deleted
        self._registered_workers: dict[str, WorkerInfo] = {}

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def register(self, info: WorkerInfo) -> None:
        """Register worker with Session Router.

        Creates worker entry in Redis with initial state.

        Args:
            info: Worker registration information
        """
        r = await self._get_redis()
        worker_key = f"{WORKER_KEY_PREFIX}{info.worker_id}"

        # Set worker state
        await r.hset(
            worker_key,
            mapping={
                "endpoint": info.endpoint,
                "status": "ready",
                "capacity": str(info.capacity),
                "active_sessions": "0",
                "gpu_memory_used": "0GB",
                "gpu_memory_total": "0GB",
                "models_loaded": json.dumps(info.models),
                "languages_supported": json.dumps(info.languages),
                "engine": info.engine,
                "supports_vocabulary": "true" if info.supports_vocabulary else "false",
                "last_heartbeat": datetime.now(UTC).isoformat(),
                "started_at": datetime.now(UTC).isoformat(),
            },
        )

        # Add to worker set
        await r.sadd(WORKER_SET_KEY, info.worker_id)

        # Store registration info for re-registration if key is deleted
        self._registered_workers[info.worker_id] = info

        # Publish registration event
        await r.publish(
            EVENTS_CHANNEL,
            json.dumps(
                {
                    "type": "worker.registered",
                    "worker_id": info.worker_id,
                    "endpoint": info.endpoint,
                    "capacity": info.capacity,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        )

        logger.info(
            "worker_registered", worker_id=info.worker_id, capacity=info.capacity
        )

    async def heartbeat(
        self,
        worker_id: str,
        active_sessions: int,
        gpu_memory_used: str,
        status: str = "ready",
    ) -> None:
        """Send heartbeat update.

        Updates worker state and last_heartbeat timestamp. If the Redis key
        was deleted (e.g., Redis restart, manual deletion), re-populates all
        registration fields from stored info.

        Args:
            worker_id: Worker identifier
            active_sessions: Current number of active sessions
            gpu_memory_used: GPU memory usage string (e.g., "4.2GB")
            status: Worker status ("ready", "busy", "draining")
        """
        r = await self._get_redis()
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        now = datetime.now(UTC).isoformat()

        # Check if key is missing or incomplete
        existing_data = await r.hget(worker_key, "endpoint")
        needs_reregistration = existing_data is None

        if needs_reregistration and worker_id in self._registered_workers:
            # Re-populate all registration fields
            info = self._registered_workers[worker_id]
            await r.hset(
                worker_key,
                mapping={
                    "endpoint": info.endpoint,
                    "status": status,
                    "capacity": str(info.capacity),
                    "active_sessions": str(active_sessions),
                    "gpu_memory_used": gpu_memory_used,
                    "gpu_memory_total": "0GB",
                    "models_loaded": json.dumps(info.models),
                    "languages_supported": json.dumps(info.languages),
                    "engine": info.engine,
                    "last_heartbeat": now,
                    "started_at": now,
                },
            )

            # Re-add to worker set (idempotent)
            await r.sadd(WORKER_SET_KEY, worker_id)

            logger.info(
                "worker_reregistered",
                worker_id=worker_id,
                endpoint=info.endpoint,
                reason="key_missing",
            )
        else:
            # Normal heartbeat - just update dynamic fields
            await r.hset(
                worker_key,
                mapping={
                    "status": status,
                    "active_sessions": str(active_sessions),
                    "gpu_memory_used": gpu_memory_used,
                    "last_heartbeat": now,
                },
            )

        logger.debug("heartbeat", worker_id=worker_id, active_sessions=active_sessions)

    async def session_started(self, worker_id: str, session_id: str) -> None:
        """Notify that a session has started on this worker.

        Note: The session set is managed exclusively by the Gateway's
        SessionAllocator (acquire_worker/release_worker). This method
        only logs for observability.

        Args:
            worker_id: Worker identifier
            session_id: Session identifier
        """
        # Note: Session set management is handled by Gateway's SessionAllocator
        # to avoid race conditions during Gateway crash recovery.
        # See: dalston/session_router/allocator.py

        logger.debug("session_started", worker_id=worker_id, session_id=session_id)

    async def session_ended(
        self,
        worker_id: str,
        session_id: str,
        duration: float,
        status: str,
    ) -> None:
        """Notify that a session has ended.

        Publishes event for monitoring. Session set cleanup is handled by
        the Gateway's SessionAllocator (release_worker) or HealthMonitor
        reconciliation.

        Args:
            worker_id: Worker identifier
            session_id: Session identifier
            duration: Session duration in seconds
            status: End status ("completed" or "error")
        """
        r = await self._get_redis()

        # Note: Session set management is handled by Gateway's SessionAllocator
        # to avoid race conditions during Gateway crash recovery.
        # See: dalston/session_router/allocator.py and health.py

        # Publish session end event for monitoring
        await r.publish(
            EVENTS_CHANNEL,
            json.dumps(
                {
                    "type": "session.ended",
                    "worker_id": worker_id,
                    "session_id": session_id,
                    "duration": duration,
                    "status": status,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        )

        logger.debug(
            "session_ended",
            worker_id=worker_id,
            session_id=session_id,
            duration=round(duration, 1),
            status=status,
        )

    async def unregister(self, worker_id: str) -> None:
        """Unregister worker on shutdown.

        Removes worker from registry and cleans up related keys.

        Args:
            worker_id: Worker identifier
        """
        r = await self._get_redis()
        worker_key = f"{WORKER_KEY_PREFIX}{worker_id}"
        sessions_key = f"{WORKER_KEY_PREFIX}{worker_id}{WORKER_SESSIONS_SUFFIX}"

        # Remove from worker set
        await r.srem(WORKER_SET_KEY, worker_id)

        # Delete worker state
        await r.delete(worker_key)

        # Delete sessions set
        await r.delete(sessions_key)

        # Remove from local cache
        self._registered_workers.pop(worker_id, None)

        # Publish unregistration event
        await r.publish(
            EVENTS_CHANNEL,
            json.dumps(
                {
                    "type": "worker.unregistered",
                    "worker_id": worker_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        )

        logger.info("worker_unregistered", worker_id=worker_id)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
