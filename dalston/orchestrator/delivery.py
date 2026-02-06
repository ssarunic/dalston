"""Webhook delivery worker.

Polls the webhook_deliveries table for pending deliveries and processes them
with retry logic. This provides crash-resilient webhook delivery.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.config import Settings
from dalston.db.models import WebhookDeliveryModel, WebhookEndpointModel
from dalston.gateway.services.webhook import WebhookService

logger = structlog.get_logger()

# Retry delays in seconds: immediate, 30s, 2m, 10m, 1h
RETRY_DELAYS = [0, 30, 120, 600, 3600]
MAX_ATTEMPTS = 5
POLL_INTERVAL = 2.0  # seconds
MAX_CONCURRENT = 10

# Auto-disable thresholds (ElevenLabs style)
AUTO_DISABLE_FAILURE_THRESHOLD = 10  # consecutive failures
AUTO_DISABLE_SUCCESS_WINDOW_DAYS = 7  # last success must be within this window


class DeliveryWorker:
    """Worker that polls and delivers pending webhooks."""

    def __init__(
        self,
        session_factory,
        settings: Settings,
    ):
        """Initialize the delivery worker.

        Args:
            session_factory: Async SQLAlchemy session factory
            settings: Application settings
        """
        self._session_factory = session_factory
        self._settings = settings
        self._webhook_service = WebhookService(secret=settings.webhook_secret)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the delivery worker as a background task."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("delivery_worker_started")

    async def stop(self):
        """Stop the delivery worker gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("delivery_worker_stopped")

    async def _run_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_and_deliver()
            except Exception as e:
                logger.error("delivery_worker_error", error=str(e))
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_and_deliver(self):
        """Poll for pending deliveries and process them."""
        async with self._session_factory() as db:
            # Select pending deliveries that are due for retry
            # Use FOR UPDATE SKIP LOCKED to prevent duplicate processing
            query = (
                select(WebhookDeliveryModel)
                .where(
                    and_(
                        WebhookDeliveryModel.status == "pending",
                        WebhookDeliveryModel.next_retry_at <= datetime.now(UTC),
                    )
                )
                .order_by(WebhookDeliveryModel.next_retry_at)
                .limit(MAX_CONCURRENT)
                .with_for_update(skip_locked=True)
            )

            result = await db.execute(query)
            deliveries = list(result.scalars().all())

            if not deliveries:
                return

            logger.debug(
                "delivery_worker_processing",
                count=len(deliveries),
            )

            # Process deliveries concurrently
            tasks = [self._process_delivery(db, delivery) for delivery in deliveries]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log any exceptions that were caught
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "delivery_task_exception",
                        delivery_id=str(deliveries[i].id),
                        error=str(result),
                        error_type=type(result).__name__,
                    )

    async def _process_delivery(self, db: AsyncSession, delivery: WebhookDeliveryModel):
        """Process a single delivery attempt."""
        log = logger.bind(
            delivery_id=str(delivery.id),
            event_type=delivery.event_type,
            attempts=delivery.attempts,
        )

        try:
            await self._do_delivery(db, delivery, log)
        except Exception as e:
            await db.rollback()
            log.error(
                "delivery_processing_error", error=str(e), error_type=type(e).__name__
            )
            raise

    async def _do_delivery(self, db: AsyncSession, delivery: WebhookDeliveryModel, log):
        """Execute the actual delivery logic within a transaction."""
        endpoint = None

        # Determine URL and secret
        if delivery.endpoint_id:
            # Registered endpoint - get URL and secret from endpoint
            endpoint_query = select(WebhookEndpointModel).where(
                WebhookEndpointModel.id == delivery.endpoint_id
            )
            result = await db.execute(endpoint_query)
            endpoint = result.scalar_one_or_none()

            if endpoint is None:
                log.error("delivery_endpoint_not_found")
                delivery.status = "failed"
                delivery.last_error = "Endpoint not found"
                await db.commit()
                return

            url = endpoint.url
            secret = endpoint.signing_secret
            log = log.bind(endpoint_id=str(endpoint.id), url=url)
        else:
            # Per-job webhook - use url_override and global secret
            if not delivery.url_override:
                log.error("delivery_no_url")
                delivery.status = "failed"
                delivery.last_error = "No URL configured"
                await db.commit()
                return

            url = delivery.url_override
            secret = self._settings.webhook_secret
            log = log.bind(url=url)

        log.info("delivering_webhook")

        # Attempt delivery (single attempt, no retries - we handle retries via the table)
        success, status_code, error = await self._webhook_service.deliver(
            url=url,
            payload=delivery.payload,
            max_retries=0,  # No in-memory retries, we use the delivery table
            secret=secret,
            delivery_id=delivery.id,
        )

        # Update delivery record
        delivery.attempts += 1
        delivery.last_attempt_at = datetime.now(UTC)
        delivery.last_status_code = status_code
        delivery.last_error = error

        if success:
            delivery.status = "success"
            delivery.next_retry_at = None
            log.info("webhook_delivered_successfully", status_code=status_code)

            # Reset endpoint failure tracking on success (atomic update to prevent races)
            if delivery.endpoint_id and endpoint:
                await db.execute(
                    update(WebhookEndpointModel)
                    .where(WebhookEndpointModel.id == endpoint.id)
                    .values(
                        consecutive_failures=0,
                        last_success_at=datetime.now(UTC),
                    )
                )

        elif delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = "failed"
            delivery.next_retry_at = None
            log.warning(
                "webhook_delivery_exhausted",
                total_attempts=delivery.attempts,
                last_error=error,
            )

            # Increment endpoint failure count atomically and check auto-disable
            if delivery.endpoint_id and endpoint:
                await db.execute(
                    update(WebhookEndpointModel)
                    .where(WebhookEndpointModel.id == endpoint.id)
                    .values(
                        consecutive_failures=WebhookEndpointModel.consecutive_failures
                        + 1,
                    )
                )
                # Refresh endpoint to get updated failure count for auto-disable check
                await db.refresh(endpoint)
                await self._check_auto_disable(db, endpoint, log)

        else:
            # Schedule retry
            delay = RETRY_DELAYS[min(delivery.attempts, len(RETRY_DELAYS) - 1)]
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            log.info(
                "webhook_retry_scheduled",
                next_attempt=delivery.attempts + 1,
                delay_seconds=delay,
            )

        await db.commit()

    async def _check_auto_disable(
        self, db: AsyncSession, endpoint: WebhookEndpointModel, log
    ) -> None:
        """Check if endpoint should be auto-disabled due to repeated failures.

        Auto-disables when:
        - 10+ consecutive failures AND
        - Never had a successful delivery OR last success was > 7 days ago
        """
        if endpoint.consecutive_failures < AUTO_DISABLE_FAILURE_THRESHOLD:
            return

        # Check if last success was within the window
        if endpoint.last_success_at:
            window_start = datetime.now(UTC) - timedelta(
                days=AUTO_DISABLE_SUCCESS_WINDOW_DAYS
            )
            if endpoint.last_success_at > window_start:
                # Had a recent success, don't auto-disable
                return

        # Auto-disable the endpoint (atomic update)
        await db.execute(
            update(WebhookEndpointModel)
            .where(WebhookEndpointModel.id == endpoint.id)
            .values(
                is_active=False,
                disabled_reason="auto_disabled",
            )
        )
        log.warning(
            "webhook_endpoint_auto_disabled",
            endpoint_id=str(endpoint.id),
            consecutive_failures=endpoint.consecutive_failures,
            last_success_at=str(endpoint.last_success_at)
            if endpoint.last_success_at
            else None,
        )


async def create_webhook_delivery(
    db: AsyncSession,
    endpoint_id: str | None,
    job_id: str | None,
    event_type: str,
    payload: dict,
    url_override: str | None = None,
) -> WebhookDeliveryModel:
    """Create a webhook delivery record.

    Args:
        db: Database session
        endpoint_id: Registered endpoint UUID (or None for per-job webhooks)
        job_id: Job UUID that triggered this delivery
        event_type: Event type (e.g., transcription.completed)
        payload: Webhook payload to deliver
        url_override: URL for per-job webhooks (when endpoint_id is None)

    Returns:
        Created delivery record
    """
    delivery = WebhookDeliveryModel(
        endpoint_id=endpoint_id,
        job_id=job_id,
        event_type=event_type,
        payload=payload,
        url_override=url_override,
        status="pending",
        attempts=0,
        next_retry_at=datetime.now(UTC),
    )
    db.add(delivery)
    await db.flush()  # Get the ID without committing
    return delivery
