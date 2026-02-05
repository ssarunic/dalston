"""Orchestrator entry point.

Runs the main event loop that:
1. Subscribes to Redis pub/sub channel 'dalston:events'
2. Dispatches events to appropriate handlers
3. Manages graceful shutdown
"""

import asyncio
import json
import signal
import sys
from uuid import UUID

import structlog
from redis import asyncio as aioredis

import dalston.logging
from dalston.common.events import EVENTS_CHANNEL
from dalston.config import get_settings
from dalston.db.models import JobModel
from dalston.db.session import async_session, init_db
from dalston.gateway.services.storage import StorageService
from dalston.gateway.services.webhook import WebhookService
from dalston.orchestrator.handlers import (
    handle_job_created,
    handle_task_completed,
    handle_task_failed,
)

# Configure structured logging via shared module
dalston.logging.configure("orchestrator")

logger = structlog.get_logger()

# Shutdown flag
_shutdown_event: asyncio.Event | None = None


async def orchestrator_loop() -> None:
    """Main event loop for the orchestrator.

    Subscribes to Redis pub/sub and dispatches events to handlers.
    """
    global _shutdown_event
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
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=1.0,
                )

                if message is None:
                    continue

                if message["type"] != "message":
                    continue

                await _dispatch_event(message["data"], redis, settings)

            except TimeoutError:
                # Normal timeout, check shutdown flag and continue
                continue
            except Exception as e:
                logger.exception("event_processing_error", error=str(e))
                # Continue processing other events
                await asyncio.sleep(0.1)

    finally:
        await pubsub.unsubscribe(EVENTS_CHANNEL)
        await pubsub.close()
        await redis.close()
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

    # Bind correlation IDs from the event into structlog context
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(_service_name="orchestrator")
    if "request_id" in event:
        structlog.contextvars.bind_contextvars(request_id=event["request_id"])

    log.debug("received_event", payload=event)

    # Get a fresh database session for each event
    async with async_session() as db:
        try:
            if event_type == "job.created":
                job_id = UUID(event["job_id"])
                await handle_job_created(job_id, db, redis, settings)

            elif event_type == "task.completed":
                task_id = UUID(event["task_id"])
                await handle_task_completed(task_id, db, redis, settings)

            elif event_type == "task.failed":
                task_id = UUID(event["task_id"])
                error = event.get("error", "Unknown error")
                await handle_task_failed(task_id, error, db, redis, settings)

            elif event_type == "job.completed":
                job_id = UUID(event["job_id"])
                await _handle_job_webhook(job_id, "completed", db, settings)

            elif event_type == "job.failed":
                job_id = UUID(event["job_id"])
                error = event.get("error", "Unknown error")
                await _handle_job_webhook(job_id, "failed", db, settings, error)

            else:
                log.debug("unknown_event_type")

        except Exception as e:
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

    if not job.webhook_url:
        log.debug("no_webhook_url_configured")
        return

    log = log.bind(webhook_url=job.webhook_url)
    log.info("delivering_webhook")

    # Initialize webhook service
    webhook_service = WebhookService(secret=settings.webhook_secret)

    # Get transcript data for completed jobs
    text = None
    duration = None
    if status == "completed":
        try:
            storage = StorageService(settings)
            transcript = await storage.get_transcript(job_id)
            if transcript:
                text = transcript.get("text")
                metadata = transcript.get("metadata", {})
                duration = metadata.get("duration")
        except Exception as e:
            log.warning("failed_to_fetch_transcript_for_webhook", error=str(e))

    # Build and deliver webhook
    event_type = f"transcription.{status}"
    payload = webhook_service.build_payload(
        event=event_type,
        job_id=job_id,
        status=status,
        text=text,
        duration=duration,
        error=error,
        webhook_metadata=job.webhook_metadata,
    )

    success = await webhook_service.deliver(job.webhook_url, payload)

    if success:
        log.info("webhook_delivered_successfully")
    else:
        log.warning("webhook_delivery_failed")
        # Note: Retry logic will be added in M05.4


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
