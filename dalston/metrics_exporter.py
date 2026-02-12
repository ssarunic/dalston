"""Redis queue metrics exporter for Prometheus.

Periodically reads Redis queue depths and exposes them at /metrics
for Prometheus scraping.

Usage:
    python -m dalston.metrics_exporter

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    METRICS_PORT: Port for /metrics endpoint (default: 9100)
    SCRAPE_INTERVAL: Interval between queue depth reads (default: 15 seconds)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime

import structlog
from aiohttp import web
from redis import asyncio as aioredis

import dalston.logging
import dalston.metrics

# Configure structured logging
dalston.logging.configure("metrics-exporter")
logger = structlog.get_logger()

# Known engine queue patterns
QUEUE_KEY_PATTERN = "dalston:queue:{engine_id}"
KNOWN_ENGINES = [
    "audio-prepare",
    "faster-whisper",
    "whisperx-align",
    "pyannote-3.1",
    "pyannote-4.0",
    "final-merger",
    "parakeet",
]

# Global state
_shutdown_event: asyncio.Event | None = None
_redis: aioredis.Redis | None = None

# Task metadata key pattern
TASK_METADATA_KEY = "dalston:task:{task_id}"


async def _get_oldest_task_age(queue_key: str) -> float:
    """Get the age of the oldest task in a queue.

    Uses LINDEX to peek at the oldest task (right end of list, index -1)
    and reads its enqueued_at timestamp from metadata.

    Args:
        queue_key: Redis key for the engine queue

    Returns:
        Age in seconds, or 0 if queue is empty or timestamp unavailable
    """
    if _redis is None:
        return 0.0

    try:
        # Peek at oldest task (engines pop from right with BRPOP, so oldest is at index -1)
        oldest_task_id = await _redis.lindex(queue_key, -1)
        if not oldest_task_id:
            return 0.0

        # Fetch task metadata
        metadata_key = TASK_METADATA_KEY.format(task_id=oldest_task_id)
        enqueued_at_str = await _redis.hget(metadata_key, "enqueued_at")
        if not enqueued_at_str:
            return 0.0

        # Calculate age
        enqueued_at = datetime.fromisoformat(enqueued_at_str)
        now = datetime.now(UTC)
        return (now - enqueued_at).total_seconds()

    except (ValueError, TypeError):
        return 0.0


async def collect_queue_metrics() -> None:
    """Collect queue depth metrics from Redis."""
    global _redis

    if _redis is None:
        return

    try:
        # Test connectivity
        await _redis.ping()
        dalston.metrics.set_redis_connected(True)

        # Collect queue depths for known engines
        for engine_id in KNOWN_ENGINES:
            queue_key = QUEUE_KEY_PATTERN.format(engine_id=engine_id)
            depth = await _redis.llen(queue_key)
            dalston.metrics.set_queue_depth(engine_id, depth)

            # Calculate oldest task age from enqueued_at timestamp (M20)
            oldest_age = await _get_oldest_task_age(queue_key)
            dalston.metrics.set_queue_oldest_task_age(engine_id, oldest_age)

    except Exception as e:
        logger.error("redis_error", error=str(e))
        dalston.metrics.set_redis_connected(False)


async def metrics_collector_loop(interval: float) -> None:
    """Background loop that collects metrics periodically.

    Args:
        interval: Seconds between collections
    """
    global _shutdown_event

    while not _shutdown_event.is_set():
        await collect_queue_metrics()
        await asyncio.sleep(interval)


async def handle_metrics(request: web.Request) -> web.Response:
    """Handle /metrics endpoint."""
    from prometheus_client import generate_latest

    # Use text/plain without charset in content_type (aiohttp handles charset separately)
    return web.Response(
        body=generate_latest(),
        content_type="text/plain",
        charset="utf-8",
    )


async def handle_health(request: web.Request) -> web.Response:
    """Handle /health endpoint."""
    return web.Response(text="ok")


async def exporter_main() -> None:
    """Main entry point for the metrics exporter."""
    global _shutdown_event, _redis

    _shutdown_event = asyncio.Event()

    # Load configuration from environment
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    port = int(os.environ.get("METRICS_PORT", "9100"))
    interval = float(os.environ.get("SCRAPE_INTERVAL", "15"))

    logger.info(
        "metrics_exporter_starting",
        redis_url=redis_url,
        port=port,
        interval=interval,
    )

    # Initialize metrics
    dalston.metrics.init_queue_metrics()

    # Connect to Redis
    _redis = aioredis.from_url(redis_url, decode_responses=True)

    # Test connectivity
    try:
        await _redis.ping()
        logger.info("redis_connected")
        dalston.metrics.set_redis_connected(True)
    except Exception as e:
        logger.warning("redis_connection_failed", error=str(e))
        dalston.metrics.set_redis_connected(False)

    # Start metrics collector background task
    collector_task = asyncio.create_task(metrics_collector_loop(interval))

    # Start HTTP server
    app = web.Application()
    app.router.add_get("/metrics", handle_metrics)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("metrics_exporter_ready", port=port)

    # Wait for shutdown
    while not _shutdown_event.is_set():
        await asyncio.sleep(1)

    # Cleanup
    collector_task.cancel()
    try:
        await collector_task
    except asyncio.CancelledError:
        pass

    await runner.cleanup()
    if _redis:
        await _redis.close()

    logger.info("metrics_exporter_stopped")


def handle_shutdown(signum, frame) -> None:
    """Handle shutdown signals."""
    logger.info("shutdown_signal_received", signal=signum)
    if _shutdown_event:
        _shutdown_event.set()


def main() -> None:
    """Entry point for the metrics exporter.

    Run with: python -m dalston.metrics_exporter
    """
    # Setup signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    try:
        asyncio.run(exporter_main())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
        sys.exit(0)


if __name__ == "__main__":
    main()
