"""Health monitoring for real-time workers.

Monitors worker heartbeats and marks stale workers as offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import redis.asyncio as redis

from dalston.session_router.registry import (
    EVENTS_CHANNEL,
    WorkerRegistry,
)

logger = logging.getLogger(__name__)


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
        registry: WorkerRegistry,
    ) -> None:
        """Initialize health monitor.

        Args:
            redis_client: Async Redis client (for publishing events)
            registry: Worker registry for reading worker state
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
        logger.info("Health monitor started")

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

        logger.info("Health monitor stopped")

    async def _run_loop(self) -> None:
        """Main health check loop."""
        while self._running:
            try:
                await self.check_workers()
            except Exception as e:
                logger.error(f"Error in health check: {e}")

            await asyncio.sleep(self.CHECK_INTERVAL)

    async def check_workers(self) -> None:
        """Check all workers and mark stale ones offline.

        Called periodically by the background loop.
        """
        workers = await self._registry.get_workers()
        now = datetime.now(UTC)

        for worker in workers:
            # Skip already offline workers
            if worker.status == "offline":
                continue

            # Calculate heartbeat age
            age = (now - worker.last_heartbeat).total_seconds()

            if age > self.HEARTBEAT_TIMEOUT:
                logger.warning(
                    f"Worker {worker.worker_id} heartbeat stale "
                    f"({age:.0f}s > {self.HEARTBEAT_TIMEOUT}s)"
                )

                # Mark offline
                await self._registry.mark_worker_offline(worker.worker_id)

                # Get affected sessions
                session_ids = await self._registry.get_worker_session_ids(
                    worker.worker_id
                )

                # Publish event for each affected session
                for session_id in session_ids:
                    await self._publish_worker_offline_event(
                        worker.worker_id, session_id
                    )

    async def _publish_worker_offline_event(
        self,
        worker_id: str,
        session_id: str,
    ) -> None:
        """Publish worker offline event to Redis pub/sub.

        Gateway subscribes to these events to notify affected clients.

        Args:
            worker_id: Worker that went offline
            session_id: Affected session
        """
        await self._redis.publish(
            EVENTS_CHANNEL,
            json.dumps(
                {
                    "type": "worker.offline",
                    "worker_id": worker_id,
                    "session_id": session_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        )

        logger.info(
            f"Published worker.offline event: worker={worker_id}, session={session_id}"
        )

    @property
    def is_running(self) -> bool:
        """Whether the health monitor is running."""
        return self._running
