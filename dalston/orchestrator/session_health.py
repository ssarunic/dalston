"""Health monitoring for real-time workers (moved from session_router, M66).

Monitors worker heartbeats and marks stale workers as offline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

import dalston.metrics
from dalston.common.registry import UnifiedEngineRegistry
from dalston.orchestrator.realtime_registry import (
    ACTIVE_SESSIONS_KEY,
    EVENTS_CHANNEL,
    INSTANCE_KEY_PREFIX,
    INSTANCE_SESSIONS_SUFFIX,
    INSTANCE_SET_KEY,
    SESSION_KEY_PREFIX,
)

logger = structlog.get_logger()


class HealthMonitor:
    """Monitors worker health via heartbeat timeout.

    Runs a background loop that checks worker heartbeats every 10 seconds.
    Workers that haven't sent a heartbeat in 30 seconds are marked offline.

    Example:
        monitor = HealthMonitor(redis_client, registry)

        # Start monitoring in background
        await monitor.start()

        # ... do other work ...

        # Stop monitoring
        await monitor.stop()
    """

    CHECK_INTERVAL = 10  # seconds between health checks
    HEARTBEAT_TIMEOUT = 30  # seconds before marking worker offline

    def __init__(
        self,
        redis_client: redis.Redis,
        registry: UnifiedEngineRegistry,
    ) -> None:
        """Initialize health monitor.

        Args:
            redis_client: Async Redis client (for publishing events)
            registry: Engine registry for reading worker state
        """
        self._redis = redis_client
        self._registry = registry
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the health check background loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("health_monitor_started")

    async def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("health_monitor_stopped")

    async def _run_loop(self) -> None:
        """Main health check loop."""
        while self._running:
            try:
                await self.check_workers()
                await self.reconcile_orphaned_sessions()
            except Exception as e:
                logger.error("health_check_error", error=str(e))

            await asyncio.sleep(self.CHECK_INTERVAL)

    async def check_workers(self) -> None:
        """Check all workers and mark stale ones offline.

        Called periodically by the background loop.
        """
        workers = [
            e
            for e in await self._registry.get_all()
            if e.supports_interface("realtime")
        ]
        now = datetime.now(UTC)

        # Track worker counts for metrics
        total_workers = len(workers)
        healthy_workers = 0

        for worker in workers:
            # Skip already offline workers
            if worker.status == "offline":
                continue

            # Calculate heartbeat age
            if worker.last_heartbeat is None:
                continue
            age = (now - worker.last_heartbeat).total_seconds()

            if age > self.HEARTBEAT_TIMEOUT:
                logger.warning(
                    "instance_heartbeat_stale",
                    instance=worker.instance,
                    age_seconds=round(age),
                    timeout=self.HEARTBEAT_TIMEOUT,
                )

                # Mark offline
                await self._registry.mark_instance_offline(worker.instance)

                # Get affected sessions via direct Redis (session tracking keys)
                sessions_key = (
                    f"{INSTANCE_KEY_PREFIX}{worker.instance}{INSTANCE_SESSIONS_SUFFIX}"
                )
                session_ids = await self._redis.smembers(sessions_key)

                # Publish event for each affected session
                for session_id in session_ids:
                    await self._publish_instance_offline_event(
                        worker.instance, session_id
                    )
            else:
                # Worker is healthy (not stale)
                healthy_workers += 1

        # Update metrics (M20)
        dalston.metrics.set_session_router_workers_registered(total_workers)
        dalston.metrics.set_session_router_workers_healthy(healthy_workers)

    async def reconcile_orphaned_sessions(self) -> int:
        """Clean up orphaned sessions from crashed Gateway instances.

        When a Gateway crashes, the finally block that calls release_worker()
        never executes. The session key expires via TTL (5 min), but related
        state remains:
        - ACTIVE_SESSIONS_KEY set still contains the session_id
        - Worker's session set still contains the session_id
        - Worker's active_sessions counter is still elevated

        This method periodically scans for sessions in ACTIVE_SESSIONS_KEY
        that no longer have a corresponding session key (expired), and cleans
        up the orphaned state.

        Returns:
            Number of orphaned sessions cleaned up
        """
        cleaned_count = 0

        # Get all sessions marked as active
        active_sessions = await self._redis.smembers(ACTIVE_SESSIONS_KEY)
        if not active_sessions:
            return 0

        # Check each session
        for session_id in active_sessions:
            session_key = f"{SESSION_KEY_PREFIX}{session_id}"

            # If session key still exists, it's a live session
            if await self._redis.exists(session_key):
                continue

            # Session key expired - this is an orphaned session
            logger.info(
                "orphaned_session_detected",
                session_id=session_id,
                reason="session_key_expired",
            )

            # Find which instance had this session
            instances = await self._redis.smembers(INSTANCE_SET_KEY)
            for instance in instances:
                sessions_key = (
                    f"{INSTANCE_KEY_PREFIX}{instance}{INSTANCE_SESSIONS_SUFFIX}"
                )
                if await self._redis.sismember(sessions_key, session_id):
                    # Found the instance - decrement counter and remove from set
                    instance_key = f"{INSTANCE_KEY_PREFIX}{instance}"
                    new_count = await self._redis.hincrby(
                        instance_key, "active_sessions", -1
                    )

                    # Ensure counter doesn't go negative
                    if new_count < 0:
                        await self._redis.hset(instance_key, "active_sessions", 0)
                        new_count = 0

                    # Remove from instance's session set
                    await self._redis.srem(sessions_key, session_id)

                    # Update metrics
                    dalston.metrics.set_session_router_sessions_active(
                        instance, max(0, new_count)
                    )

                    logger.info(
                        "orphaned_session_cleaned_from_instance",
                        session_id=session_id,
                        instance=instance,
                        new_active_sessions=new_count,
                    )
                    break

            # Remove from active sessions index
            await self._redis.srem(ACTIVE_SESSIONS_KEY, session_id)
            cleaned_count += 1

            logger.info(
                "orphaned_session_cleaned",
                session_id=session_id,
            )

        if cleaned_count > 0:
            logger.info(
                "orphaned_sessions_reconciliation_complete",
                cleaned_count=cleaned_count,
            )

        return cleaned_count

    async def _publish_instance_offline_event(
        self,
        instance: str,
        session_id: str,
    ) -> None:
        """Publish instance offline event to Redis pub/sub.

        Gateway subscribes to these events to notify affected clients.

        Args:
            instance: Instance that went offline
            session_id: Affected session
        """
        await self._redis.publish(
            EVENTS_CHANNEL,
            json.dumps(
                {
                    "type": "instance.offline",
                    "instance": instance,
                    "session_id": session_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        )

        logger.info(
            "published_instance_offline_event", instance=instance, session_id=session_id
        )

    @property
    def is_running(self) -> bool:
        """Whether the health monitor is running."""
        return self._running
