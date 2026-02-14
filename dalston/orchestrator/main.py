"""Orchestrator entry point.

Runs the main event loop that:
1. Subscribes to Redis pub/sub channel 'dalston:events'
2. Dispatches events to appropriate handlers
3. Manages graceful shutdown
"""

import asyncio
import json
import os
import signal
import sys
from uuid import UUID

import structlog
from aiohttp import web
from redis import asyncio as aioredis

import dalston.logging
import dalston.metrics
import dalston.telemetry
from dalston.common.audit import AuditService
from dalston.common.events import EVENTS_CHANNEL
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
)

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
    global _shutdown_event, _delivery_worker, _cleanup_worker
    _shutdown_event = asyncio.Event()

    settings = get_settings()

    logger.info(
        "orchestrator_starting",
        redis_url=settings.redis_url,
        events_channel=EVENTS_CHANNEL,
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
    pubsub = redis.pubsub()

    try:
        # Subscribe to events channel
        await pubsub.subscribe(EVENTS_CHANNEL)
        logger.info("subscribed_to_events", channel=EVENTS_CHANNEL)

        # Event loop
        while not _shutdown_event.is_set():
            try:
                # Get message with timeout to allow shutdown check
                # Note: timeout must be passed directly to get_message() for proper blocking
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )

                if message is None:
                    # Timeout expired with no message, loop back to check shutdown flag
                    continue

                if message["type"] != "message":
                    continue

                await _dispatch_event(message["data"], redis, settings)

            except Exception as e:
                logger.exception("event_processing_error", error=str(e))
                # Continue processing other events
                await asyncio.sleep(0.1)

    finally:
        # Stop delivery worker
        if _delivery_worker:
            await _delivery_worker.stop()

        # Stop cleanup worker
        if _cleanup_worker:
            await _cleanup_worker.stop()

        # Stop metrics server
        await _stop_metrics_server()

        await pubsub.unsubscribe(EVENTS_CHANNEL)
        await pubsub.close()
        await redis.close()
        dalston.telemetry.shutdown_tracing()
        logger.info("orchestrator_stopped")


async def _dispatch_event(
    data: str,
    redis: aioredis.Redis,
    settings,
) -> None:
    """Parse and dispatch an event to the appropriate handler.

    Args:
        data: Raw JSON event data
        redis: Redis client
        settings: Application settings
    """
    try:
        event = json.loads(data)
    except json.JSONDecodeError as e:
        logger.error("invalid_event_json", error=str(e), data=data[:100])
        return

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
    trace_context = event.pop("_trace_context", {})

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
                    await handle_job_created(job_id, db, redis, settings)

                elif event_type == "task.started":
                    task_id = UUID(event["task_id"])
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    await handle_task_started(task_id, db)

                elif event_type == "task.completed":
                    task_id = UUID(event["task_id"])
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    await handle_task_completed(task_id, db, redis, settings)

                elif event_type == "task.failed":
                    task_id = UUID(event["task_id"])
                    error = event.get("error", "Unknown error")
                    dalston.telemetry.set_span_attribute(
                        "dalston.task_id", str(task_id)
                    )
                    dalston.telemetry.set_span_attribute("dalston.error", error)
                    await handle_task_failed(task_id, error, db, redis, settings)

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
                # Don't re-raise - continue processing other events


async def _handle_job_webhook(
    job_id: UUID,
    status: str,
    db,
    settings,
    error: str | None = None,
) -> None:
    """Handle webhook delivery for completed or failed jobs.

    Creates delivery rows for:
    1. All registered endpoints subscribed to the event
    2. Per-job webhook_url if configured (legacy behavior, controlled by settings)

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
        webhook_metadata=job.webhook_metadata,
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

    # Create delivery for per-job webhook_url (legacy behavior)
    if job.webhook_url and settings.allow_per_job_webhooks:
        await create_webhook_delivery(
            db=db,
            endpoint_id=None,
            job_id=job_id,
            event_type=event_type,
            payload=payload,
            url_override=job.webhook_url,
        )
        deliveries_created += 1
        log.debug(
            "webhook_delivery_created",
            url_override=job.webhook_url,
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
