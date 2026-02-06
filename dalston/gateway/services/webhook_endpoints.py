"""Webhook endpoint management service.

Handles CRUD operations for admin-registered webhook endpoints and
delivery log queries.
"""

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import WebhookDeliveryModel, WebhookEndpointModel
from dalston.gateway.services.webhook import (
    WebhookValidationError,
    validate_webhook_url,
)

# Allowed event types for webhook subscriptions
ALLOWED_EVENTS = frozenset({"transcription.completed", "transcription.failed", "*"})


class WebhookEndpointService:
    """Service for webhook endpoint CRUD operations."""

    async def create_endpoint(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        url: str,
        events: list[str],
        description: str | None = None,
    ) -> tuple[WebhookEndpointModel, str]:
        """Create a new webhook endpoint.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            url: Webhook callback URL
            events: List of event types to subscribe to
            description: Optional human-readable description

        Returns:
            Tuple of (created endpoint, raw signing secret)

        Raises:
            WebhookValidationError: If URL is invalid or events are invalid
        """
        # Validate URL (SSRF protection)
        validate_webhook_url(url)

        # Validate events
        self._validate_events(events)

        # Generate signing secret with whsec_ prefix
        raw_secret = f"whsec_{secrets.token_urlsafe(32)}"

        endpoint = WebhookEndpointModel(
            tenant_id=tenant_id,
            url=url,
            description=description,
            events=events,
            signing_secret=raw_secret,
            is_active=True,
        )
        db.add(endpoint)
        await db.commit()
        await db.refresh(endpoint)
        return endpoint, raw_secret

    async def list_endpoints(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        is_active: bool | None = None,
    ) -> list[WebhookEndpointModel]:
        """List webhook endpoints for a tenant.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            is_active: Optional filter by active status

        Returns:
            List of webhook endpoints
        """
        query = select(WebhookEndpointModel).where(
            WebhookEndpointModel.tenant_id == tenant_id
        )
        if is_active is not None:
            query = query.where(WebhookEndpointModel.is_active == is_active)
        query = query.order_by(WebhookEndpointModel.created_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        tenant_id: UUID,
    ) -> WebhookEndpointModel | None:
        """Fetch a webhook endpoint by ID.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            tenant_id: Tenant UUID for isolation check

        Returns:
            Webhook endpoint or None if not found
        """
        query = select(WebhookEndpointModel).where(
            and_(
                WebhookEndpointModel.id == endpoint_id,
                WebhookEndpointModel.tenant_id == tenant_id,
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def update_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        tenant_id: UUID,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpointModel | None:
        """Update a webhook endpoint.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            tenant_id: Tenant UUID for isolation check
            url: New URL (optional, re-validated if provided)
            events: New events list (optional, validated if provided)
            description: New description (optional)
            is_active: New active status (optional)

        Returns:
            Updated endpoint or None if not found

        Raises:
            WebhookValidationError: If new URL or events are invalid
        """
        endpoint = await self.get_endpoint(db, endpoint_id, tenant_id)
        if endpoint is None:
            return None

        if url is not None:
            validate_webhook_url(url)
            endpoint.url = url

        if events is not None:
            self._validate_events(events)
            endpoint.events = events

        if description is not None:
            endpoint.description = description

        if is_active is not None:
            endpoint.is_active = is_active

        await db.commit()
        await db.refresh(endpoint)
        return endpoint

    async def delete_endpoint(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Delete a webhook endpoint.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            tenant_id: Tenant UUID for isolation check

        Returns:
            True if deleted, False if not found
        """
        endpoint = await self.get_endpoint(db, endpoint_id, tenant_id)
        if endpoint is None:
            return False

        await db.delete(endpoint)
        await db.commit()
        return True

    async def rotate_secret(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        tenant_id: UUID,
    ) -> tuple[WebhookEndpointModel, str] | None:
        """Rotate the signing secret for an endpoint.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            tenant_id: Tenant UUID for isolation check

        Returns:
            Tuple of (updated endpoint, new raw secret) or None if not found
        """
        endpoint = await self.get_endpoint(db, endpoint_id, tenant_id)
        if endpoint is None:
            return None

        new_secret = f"whsec_{secrets.token_urlsafe(32)}"
        endpoint.signing_secret = new_secret

        await db.commit()
        await db.refresh(endpoint)
        return endpoint, new_secret

    async def list_deliveries(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        tenant_id: UUID,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[WebhookDeliveryModel], int]:
        """List delivery attempts for an endpoint.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            tenant_id: Tenant UUID for isolation check
            status: Optional filter by status
            limit: Max results to return
            offset: Pagination offset

        Returns:
            Tuple of (deliveries list, total count)
        """
        # Verify endpoint belongs to tenant
        endpoint = await self.get_endpoint(db, endpoint_id, tenant_id)
        if endpoint is None:
            return [], 0

        # Build query
        query = select(WebhookDeliveryModel).where(
            WebhookDeliveryModel.endpoint_id == endpoint_id
        )
        if status is not None:
            query = query.where(WebhookDeliveryModel.status == status)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        # Get paginated results
        query = query.order_by(WebhookDeliveryModel.created_at.desc())
        query = query.limit(limit).offset(offset)
        result = await db.execute(query)
        deliveries = list(result.scalars().all())

        return deliveries, total

    async def retry_delivery(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        delivery_id: UUID,
        tenant_id: UUID,
    ) -> WebhookDeliveryModel | None:
        """Retry a failed delivery.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            delivery_id: Delivery UUID
            tenant_id: Tenant UUID for isolation check

        Returns:
            Updated delivery or None if not found or not in failed status

        Raises:
            ValueError: If delivery is not in failed status
        """
        # Verify endpoint belongs to tenant
        endpoint = await self.get_endpoint(db, endpoint_id, tenant_id)
        if endpoint is None:
            return None

        # Get delivery
        query = select(WebhookDeliveryModel).where(
            and_(
                WebhookDeliveryModel.id == delivery_id,
                WebhookDeliveryModel.endpoint_id == endpoint_id,
            )
        )
        result = await db.execute(query)
        delivery = result.scalar_one_or_none()
        if delivery is None:
            return None

        if delivery.status != "failed":
            raise ValueError(
                f"Can only retry failed deliveries, current status: {delivery.status}"
            )

        # Reset for retry
        delivery.status = "pending"
        delivery.next_retry_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(delivery)
        return delivery

    async def get_endpoints_for_event(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        event_type: str,
    ) -> list[WebhookEndpointModel]:
        """Get all active endpoints subscribed to an event type.

        Args:
            db: Database session
            tenant_id: Tenant UUID
            event_type: Event type to match

        Returns:
            List of matching active endpoints
        """
        # Match endpoints where events array contains the event type or wildcard
        query = select(WebhookEndpointModel).where(
            and_(
                WebhookEndpointModel.tenant_id == tenant_id,
                WebhookEndpointModel.is_active == True,  # noqa: E712
                WebhookEndpointModel.events.any(event_type)
                | WebhookEndpointModel.events.any("*"),
            )
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    def _validate_events(self, events: list[str]) -> None:
        """Validate event types against allowed set.

        Args:
            events: List of event type strings

        Raises:
            WebhookValidationError: If any event type is invalid
        """
        if not events:
            raise WebhookValidationError("At least one event type is required")

        invalid = set(events) - ALLOWED_EVENTS
        if invalid:
            raise WebhookValidationError(
                f"Invalid event types: {invalid}. "
                f"Allowed: {', '.join(sorted(ALLOWED_EVENTS))}"
            )
