"""Redis stream backlog metrics exporter for Prometheus.

Periodically reads Redis stream backlog metrics and exposes them at /metrics
for Prometheus scraping.

Usage:
    python -m dalston.metrics_exporter

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    METRICS_PORT: Port for /metrics endpoint (default: 9100)
    SCRAPE_INTERVAL: Interval between stream backlog reads (default: 15 seconds)
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
from dalston.common.streams_types import CONSUMER_GROUP

# Configure structured logging
dalston.logging.configure("metrics-exporter")
logger = structlog.get_logger()

# Known engine stream patterns
STREAM_KEY_PATTERN = "dalston:stream:{engine_id}"
KNOWN_ENGINES = [
    "audio-prepare",
    "faster-whisper",
    "phoneme-align",
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


async def _get_oldest_task_age(
    stream_key: str, depth: int, last_delivered_id: str | None
) -> float:
    """Get the age of the oldest undelivered task in a stream.

    Uses consumer-group progress to find the first message after
    `last-delivered-id`, then reads its enqueued_at timestamp.

    Args:
        stream_key: Redis key for the engine stream
        depth: Current consumer-group lag for the stream
        last_delivered_id: Consumer-group cursor used to find first undelivered task

    Returns:
        Age in seconds, or 0 if stream is empty or timestamp unavailable
    """
    if _redis is None:
        return 0.0

    try:
        if depth <= 0 or not last_delivered_id:
            return 0.0

        # Get oldest undelivered message (first message after last-delivered-id)
        messages = await _redis.xrange(
            stream_key,
            min=f"({last_delivered_id}",
            max="+",
            count=1,
        )
        if not messages:
            return 0.0

        # Extract enqueued_at from message fields
        _msg_id, fields = messages[0]
        enqueued_at_str = _decode_redis_str(fields.get("enqueued_at"))
        if not enqueued_at_str:
            # Fall back to task metadata if not in stream message
            task_id = _decode_redis_str(fields.get("task_id"))
            if task_id:
                metadata_key = TASK_METADATA_KEY.format(task_id=task_id)
                enqueued_at_str = await _redis.hget(metadata_key, "enqueued_at")
                enqueued_at_str = _decode_redis_str(enqueued_at_str)
            if not enqueued_at_str:
                return 0.0

        # Calculate age
        enqueued_at = datetime.fromisoformat(enqueued_at_str)
        now = datetime.now(UTC)
        return (now - enqueued_at).total_seconds()

    except Exception:
        return 0.0


def _decode_redis_str(value: str | bytes | None) -> str | None:
    """Decode Redis response values to strings."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return value


def _parse_nonnegative_int(value: int | str | bytes | None) -> int:
    """Parse an integer-like value and clamp to zero."""
    if value is None:
        return 0
    if isinstance(value, bytes):
        value = value.decode()
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def _get_stream_backlog_state(stream_key: str) -> tuple[int, str | None]:
    """Return (consumer-group lag, last-delivered-id) for the engine stream."""
    if _redis is None:
        return 0, None

    try:
        groups = await _redis.xinfo_groups(stream_key)
    except Exception:
        return 0, None

    for group in groups:
        group_name = _decode_redis_str(group.get("name"))
        if group_name != CONSUMER_GROUP:
            continue

        lag = _parse_nonnegative_int(group.get("lag"))
        last_delivered_id = _decode_redis_str(group.get("last-delivered-id"))
        return lag, last_delivered_id

    return 0, None


async def collect_queue_metrics() -> None:
    """Collect queue depth metrics from Redis."""
    global _redis

    if _redis is None:
        return

    try:
        # Test connectivity
        await _redis.ping()
        dalston.metrics.set_redis_connected(True)

        # Collect stream depths for known engines
        for engine_id in KNOWN_ENGINES:
            stream_key = STREAM_KEY_PATTERN.format(engine_id=engine_id)
            depth, last_delivered_id = await _get_stream_backlog_state(stream_key)
            dalston.metrics.set_queue_depth(engine_id, depth)

            # Calculate oldest task age from enqueued_at timestamp (M20)
            oldest_age = await _get_oldest_task_age(
                stream_key, depth=depth, last_delivered_id=last_delivered_id
            )
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
    port = int(os.environ.get("DALSTON_METRICS_PORT", "9100"))
    interval = float(os.environ.get("DALSTON_SCRAPE_INTERVAL", "15"))

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
