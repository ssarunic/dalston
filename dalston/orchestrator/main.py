"""Orchestrator entry point.

Runs the main event loop that:
1. Replays pending durable events on startup (crash recovery)
2. Subscribes to Redis pub/sub channel 'dalston:events'
3. Dispatches events to appropriate handlers
4. Manages graceful shutdown
"""

import asyncio
import json
import os
import signal
import sys
import time
from uuid import UUID, uuid4

import structlog
from aiohttp import web
from redis import asyncio as aioredis

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.common.audit import AuditService
from dalston.common.durable_events import (
    ack_event,
    claim_stale_pending_events,
    ensure_events_stream_group,
    read_new_events,
)

# Note: We now consume from durable stream, not pub/sub EVENTS_CHANNEL
from dalston.config import get_settings
from dalston.db.models import JobModel
from dalston.db.session import async_session, init_db
from dalston.gateway.services.storage import StorageService
from dalston.gateway.services.webhook import WebhookService
from dalston.gateway.services.webhook_endpoints import WebhookEndpointService
from dalston.orchestrator.cleanup import CleanupWorker
from dalston.orchestrator.delivery import DeliveryWorker, create_webhook_delivery
from dalston.orchestrator.handlers import (
    handle_job_cancel_requested,
    handle_job_created,
    handle_task_completed,
    handle_task_failed,
    handle_task_started,
    handle_task_wait_timeout,
)
from dalston.orchestrator.reconciler import ReconciliationSweeper
from dalston.orchestrator.registry import BatchEngineRegistry
from dalston.orchestrator.scanner import StaleTaskScanner

# Configure structured logging via shared module
dalston.logging.configure("orchestrator")

logger = structlog.get_logger()

# Configure distributed tracing (M19)
dalston.telemetry.configure_tracing("dalston-orchestrator")

# Configure Prometheus metrics (M20)
dalston.metrics.configure_metrics("orchestrator")

# Shutdown flag
_shutdown_event: asyncio.Event | None = None
_delivery_worker: DeliveryWorker | None = None
_cleanup_worker: CleanupWorker | None = None
_stale_task_scanner: StaleTaskScanner | None = None
_reconciliation_sweeper: ReconciliationSweeper | None = None
_metrics_app: web.Application | None = None
_metrics_runner: web.AppRunner | None = None


async def _handle_metrics_endpoint(request: web.Request) -> web.Response:
    """Handle /metrics endpoint for Prometheus scraping."""
    if not dalston.metrics.is_metrics_enabled():
        return web.Response(text="Metrics disabled", status=404)

    from prometheus_client import generate_latest

    # Use text/plain without charset in content_type (aiohttp handles charset separately)
    return web.Response(
        body=generate_latest(),
        content_type="text/plain",
        charset="utf-8",
    )


async def _start_metrics_server() -> None:
    """Start lightweight HTTP server for /metrics endpoint."""
    global _metrics_app, _metrics_runner

    if not dalston.metrics.is_metrics_enabled():
        return

    _metrics_app = web.Application()
    _metrics_app.router.add_get("/metrics", _handle_metrics_endpoint)
    _metrics_app.router.add_get("/health", lambda r: web.Response(text="ok"))

    _metrics_runner = web.AppRunner(_metrics_app)
    await _metrics_runner.setup()

    port = int(os.environ.get("METRICS_PORT", "8001"))
    site = web.TCPSite(_metrics_runner, "0.0.0.0", port)
    await site.start()

    logger.info("metrics_server_started", port=port)


async def _stop_metrics_server() -> None:
    """Stop the metrics HTTP server."""
    global _metrics_runner
    if _metrics_runner:
        await _metrics_runner.cleanup()
        _metrics_runner = None


async def orchestrator_loop() -> None:
    """Main event loop for the orchestrator.

    Subscribes to Redis pub/sub and dispatches events to handlers.
    """
    global \
        _shutdown_event, \
        _delivery_worker, \
        _cleanup_worker, \
        _stale_task_scanner, \
        _reconciliation_sweeper
    _shutdown_event = asyncio.Event()

    settings = get_settings()

    logger.info(
        "orchestrator_starting",
        redis_url=settings.redis_url,
        events_stream="dalston:events:stream",
    )

    # Initialize database
    await init_db()
    logger.info("database_initialized")

    # Start webhook delivery worker
    _delivery_worker = DeliveryWorker(
        session_factory=async_session,
        settings=settings,
    )
    await _delivery_worker.start()

    # Start cleanup worker (M25 - data retention)
    audit_service = AuditService(db_session_factory=async_session)
    _cleanup_worker = CleanupWorker(
        db_session_factory=async_session,
        settings=settings,
        audit_service=audit_service,
    )
    await _cleanup_worker.start()

    # Start metrics HTTP server (M20)
    await _start_metrics_server()

    # Connect to Redis
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    # Start stale task scanner (M33 - Redis Streams recovery)
    _stale_task_scanner = StaleTaskScanner(
        redis=redis,
        db_session_factory=async_session,
        settings=settings,
    )
    await _stale_task_scanner.start()

    # Start reconciliation sweeper (M33 - Streams/DB consistency)
    _reconciliation_sweeper = ReconciliationSweeper(
        redis=redis,
        db_session_factory=async_session,
        settings=settings,
    )
    await _reconciliation_sweeper.start()

    # Initialize batch engine registry
    batch_registry = BatchEngineRegistry(redis)

    # Generate unique consumer ID for this orchestrator instance
    consumer_id = f"orchestrator-{uuid4().hex[:8]}"
    logger.info("orchestrator_consumer_id", consumer_id=consumer_id)

    try:
        # Ensure durable events stream consumer group exists
        await ensure_events_stream_group(redis)

        # Claim pending events from crashed consumers (crash recovery)
        # Uses XAUTOCLAIM to take over messages idle for 60+ seconds from any consumer
        await _claim_and_replay_stale_events(
            redis, consumer_id, settings, batch_registry, is_startup=True
        )

        logger.info(
            "consuming_from_durable_stream",
            stream="dalston:events:stream",
            consumer_id=consumer_id,
        )

        # Periodic stale event claiming interval (5 minutes)
        STALE_CLAIM_INTERVAL_SECONDS = 300
        last_stale_claim_time = time.monotonic()

        # Event loop - consume from durable stream (not pub/sub)
        while not _shutdown_event.is_set():
            try:
                # Periodically claim stale events that failed processing
                # This handles transient failures without waiting for restart
                current_time = time.monotonic()
                if current_time - last_stale_claim_time >= STALE_CLAIM_INTERVAL_SECONDS:
                    last_stale_claim_time = current_time
                    try:
                        await _claim_and_replay_stale_events(
                            redis, consumer_id, settings, batch_registry
                        )
                    except Exception as e:
                        logger.warning("periodic_stale_claim_error", error=str(e))

                # Read new events from durable stream with blocking
                events = await read_new_events(
                    redis, consumer_id, count=10, block_ms=1000
                )

                if not events:
                    # Timeout expired with no events, loop back to check shutdown flag
                    continue

                for event in events:
                    message_id = event.get("id")
                    log = logger.bind(
                        event_type=event.get("type"),
                        message_id=message_id,
                    )

                    try:
                        await _dispatch_event_dict(
                            event, redis, settings, batch_registry
                        )
                        # ACK the event after successful processing
                        if message_id:
                            await ack_event(redis, message_id)
                    except Exception as e:
                        log.exception("event_processing_error", error=str(e))
                        # Don't ACK failed events - they'll be retried
                        # by claim_stale_pending_events on next startup

            except Exception as e:
                logger.exception("stream_read_error", error=str(e))
                # Continue processing after brief pause
                await asyncio.sleep(0.1)

    finally:
        # Stop stale task scanner
        if _stale_task_scanner:
            await _stale_task_scanner.stop()

        # Stop reconciliation sweeper
        if _reconciliation_sweeper:
            await _reconciliation_sweeper.stop()

        # Stop delivery worker
        if _delivery_worker:
            await _delivery_worker.stop()

        # Stop cleanup worker
        if _cleanup_worker:
            await _cleanup_worker.stop()

        # Stop metrics server
        await _stop_metrics_server()

        await redis.close()
        dalston.telemetry.shutdown_tracing()
        logger.info("orchestrator_stopped")


async def _claim_and_replay_stale_events(
    redis: aioredis.Redis,
    consumer_id: str,
    settings,
    batch_registry: BatchEngineRegistry,
    *,
    is_startup: bool = False,
) -> None:
    """Claim and replay stale events from crashed consumers.

    Uses XAUTOCLAIM to take over messages that have been idle for too long,
    regardless of which consumer they were originally delivered to. This
    enables crash recovery across different orchestrator instances.

    Args:
        redis: Redis client
        consumer_id: Consumer ID for this orchestrator instance
        settings: Application settings
        batch_registry: Batch engine registry for availability checks
        is_startup: If True, use higher limits for thorough startup recovery
    """
    logger.info("claiming_stale_events", consumer_id=consumer_id, is_startup=is_startup)

    # Use higher limits at startup for thorough recovery
    # Runtime claiming uses lower limits to avoid blocking the event loop
    if is_startup:
        max_iterations = 100  # Up to 10,000 events at startup
        count = 100
    else:
        max_iterations = 10  # Up to 1,000 events during runtime
        count = 100

    # Claim messages idle for 5+ minutes from any consumer
    # Use 5 minutes to avoid stealing from slow but healthy consumers
    # Most handlers complete well under 5 minutes; longer handlers are rare
    # This still provides reasonable recovery time for crashed consumers
    stale_events = await claim_stale_pending_events(
        redis,
        consumer_id,
        min_idle_ms=300000,
        count=count,
        max_iterations=max_iterations,
    )

    if not stale_events:
        logger.info("no_stale_events_to_claim")
        return

    logger.info("claimed_stale_events", count=len(stale_events))

    for event in stale_events:
        message_id = event.get("id")
        event_type = event.get("type")
        log = logger.bind(
            event_type=event_type,
            message_id=message_id,
            source="crash_recovery",
        )

        log.info("replaying_claimed_event")

        try:
            # Dispatch the event (event dict already has the payload fields merged)
            await _dispatch_event_dict(event, redis, settings, batch_registry)

            # ACK the event after successful processing
            if message_id:
                await ack_event(redis, message_id)
                log.debug("claimed_event_acked")

        except Exception as e:
            log.exception("claimed_event_replay_failed", error=str(e))
            # Don't ACK failed events - they'll be retried on next startup

    logger.info("stale_event_replay_complete", replayed=len(stale_events))


async def _dispatch_event_dict(
    event: dict,
    redis: aioredis.Redis,
    settings,
    batch_registry: BatchEngineRegistry,
) -> None:
    """Dispatch a pre-parsed event dict to the appropriate handler.

    Used for both pub/sub events (after JSON parsing) and durable event replay.

    Args:
        event: Parsed event dictionary
        redis: Redis client
        settings: Application settings
        batch_registry: Batch engine registry for availability checks
    """
    event_type = event.get("type")
    log = logger.bind(event_type=event_type)

    # Record event metric (M20)
    dalston.metrics.inc_orchestrator_events(event_type or "unknown")

    # Reset structlog context for this event, preserving the service name.
    dalston.logging.reset_context(
        **({"request_id": event["request_id"]} if "request_id" in event else {})
    )

    log.debug("received_event", payload=event)

    # Extract trace context from event (M19)
    trace_context = event.pop("_trace_context", {}) if "_trace_context" in event else {}

    # Create span for event handling, linked to parent trace if available
    with dalston.telemetry.span_from_context(
        f"orchestrator.handle_{event_type}",
        trace_context,
        attributes={
            "dalston.event_type": event_type,
            "dalston.request_id": event.get("request_id", ""),
        },
    ):
        # Get a fresh database session for each event
        async with async_session() as db:
            try:
                if event_type == "job.created":
                    job_id = UUID(event["job_id"])
                    dalston.telemetry.set_span_attribute("dalston.job_id", str(job_id))
                    await handle_job_created(
                        job_id, db, redis, settings, batch_registry
                    )

                elif event_type == "task.started":
                    task_id = UUID(event["task_id"])
                    engine_id = event.get("engine_id")
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    await handle_task_started(task_id, db, engine_id)

                elif event_type == "task.completed":
                    task_id = UUID(event["task_id"])
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    await handle_task_completed(
                        task_id, db, redis, settings, batch_registry
                    )

                elif event_type == "task.failed":
                    task_id = UUID(event["task_id"])
                    error = event.get("error", "Unknown error")
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    dalston.telemetry.set_span_attribute("dalston.error", error)
                    await handle_task_failed(
                        task_id, error, db, redis, settings, batch_registry
                    )

                elif event_type == "task.wait_timeout":
                    task_id = UUID(event["task_id"])
                    error = event.get("error", "Engine wait timeout")
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    dalston.telemetry.set_span_attribute("dalston.error", error)
                    await handle_task_wait_timeout(
                        task_id, error, db, redis, settings, batch_registry
                    )

                elif event_type == "job.completed":
                    job_id = UUID(event["job_id"])
                    dalston.telemetry.set_span_attribute("dalston.job_id", str(job_id))
                    await _handle_job_webhook(job_id, "completed", db, settings)

                elif event_type == "job.failed":
                    job_id = UUID(event["job_id"])
                    error = event.get("error", "Unknown error")
                    dalston.telemetry.set_span_attribute("dalston.job_id", str(job_id))
                    dalston.telemetry.set_span_attribute("dalston.error", error)
                    await _handle_job_webhook(job_id, "failed", db, settings, error)

                elif event_type == "job.cancel_requested":
                    job_id = UUID(event["job_id"])
                    dalston.telemetry.set_span_attribute("dalston.job_id", str(job_id))
                    await handle_job_cancel_requested(job_id, db, redis)

                elif event_type == "job.cancelled":
                    job_id = UUID(event["job_id"])
                    dalston.telemetry.set_span_attribute("dalston.job_id", str(job_id))
                    await _handle_job_webhook(job_id, "cancelled", db, settings)

                else:
                    log.debug("unknown_event_type")

            except Exception as e:
                dalston.telemetry.record_exception(e)
                dalston.telemetry.set_span_status_error(str(e))
                log.exception("handler_error", error=str(e))
                raise  # Re-raise for durable event replay to handle


async def _dispatch_event(
    data: str,
    redis: aioredis.Redis,
    settings,
    batch_registry: BatchEngineRegistry,
) -> None:
    """Parse and dispatch an event to the appropriate handler.

    Args:
        data: Raw JSON event data
        redis: Redis client
        settings: Application settings
        batch_registry: Batch engine registry for availability checks
    """
    try:
        event = json.loads(data)
    except json.JSONDecodeError as e:
        logger.error("invalid_event_json", error=str(e), data=data[:100])
        return

    try:
        await _dispatch_event_dict(event, redis, settings, batch_registry)
    except Exception:
        # Don't re-raise for pub/sub events - continue processing other events
        pass


async def _handle_job_webhook(
    job_id: UUID,
    status: str,
    db,
    settings,
    error: str | None = None,
) -> None:
    """Handle webhook delivery for completed or failed jobs.

    Creates delivery rows for all registered endpoints subscribed to the event.
    The actual delivery is handled by the DeliveryWorker.

    Args:
        job_id: Job UUID
        status: "completed" or "failed"
        db: Database session
        settings: Application settings
        error: Error message (for failed jobs)
    """
    log = logger.bind(job_id=str(job_id), status=status)

    # Fetch job from database
    job = await db.get(JobModel, job_id)
    if job is None:
        log.error("job_not_found_for_webhook")
        return

    event_type = f"transcription.{status}"
    log = log.bind(event_type=event_type)

    # Initialize webhook service for building payload
    webhook_service = WebhookService(secret=settings.webhook_secret)

    # Get duration for completed jobs (lightweight payload - no text)
    duration = None
    if status == "completed":
        try:
            storage = StorageService(settings)
            transcript = await storage.get_transcript(job_id)
            if transcript:
                metadata = transcript.get("metadata", {})
                duration = metadata.get("duration")
        except Exception as e:
            log.warning("failed_to_fetch_transcript_for_webhook", error=str(e))

    # Build webhook payload (Standard Webhooks format)
    payload = webhook_service.build_payload(
        event=event_type,
        job_id=job_id,
        status=status,
        duration=duration,
        error=error,
    )

    deliveries_created = 0

    # Create deliveries for registered endpoints
    endpoint_service = WebhookEndpointService()
    endpoints = await endpoint_service.get_endpoints_for_event(
        db, job.tenant_id, event_type
    )

    for endpoint in endpoints:
        await create_webhook_delivery(
            db=db,
            endpoint_id=endpoint.id,
            job_id=job_id,
            event_type=event_type,
            payload=payload,
        )
        deliveries_created += 1
        log.debug(
            "webhook_delivery_created",
            endpoint_id=str(endpoint.id),
            endpoint_url=endpoint.url,
        )

    await db.commit()

    if deliveries_created > 0:
        log.info("webhook_deliveries_queued", count=deliveries_created)
    else:
        log.debug("no_webhook_endpoints_configured")


def _handle_shutdown(signum, frame) -> None:
    """Handle shutdown signals."""
    logger.info("shutdown_signal_received", signal=signum)
    if _shutdown_event:
        _shutdown_event.set()


def main() -> None:
    """Entry point for the orchestrator.

    Run with: python -m dalston.orchestrator.main
    """
    # Setup signal handlers
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    try:
        asyncio.run(orchestrator_loop())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
        sys.exit(0)


if __name__ == "__main__":
    main()
