"""Webhook endpoint management service.

Handles CRUD operations for admin-registered webhook endpoints and
delivery log queries.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import (
    WebhookDeliveryModel,
    WebhookEndpointEvent,
    WebhookEndpointModel,
)
from dalston.gateway.security.exceptions import ResourceNotFoundError
from dalston.gateway.security.permissions import Permission
from dalston.gateway.services.webhook import (
    WebhookValidationError,
    validate_webhook_url,
)

if TYPE_CHECKING:
    from dalston.gateway.security.manager import SecurityManager
    from dalston.gateway.security.principal import Principal

# Allowed event types for webhook subscriptions
ALLOWED_EVENTS = frozenset(
    {
        "transcription.completed",
        "transcription.failed",
        "transcription.cancelled",
        "*",
    }
)


class WebhookEndpointService:
    """Service for webhook endpoint CRUD operations."""

    async def create_endpoint(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        url: str,
        events: list[str],
        description: str | None = None,
        # Ownership tracking (M45)
        created_by_key_id: UUID | None = None,
    ) -> tuple[WebhookEndpointModel, str]:
        """Create a new webhook endpoint.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            url: Webhook callback URL
            events: List of event types to subscribe to
            description: Optional human-readable description
            created_by_key_id: API key ID that created this endpoint (for ownership)

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
            signing_secret=raw_secret,
            is_active=True,
            created_by_key_id=created_by_key_id,
        )
        db.add(endpoint)
        await db.flush()
        for event_type in events:
            db.add(WebhookEndpointEvent(endpoint_id=endpoint.id, event_type=event_type))
        await db.commit()
        await db.refresh(endpoint)
        return endpoint, raw_secret

    async def list_endpoints(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        is_active: bool | None = None,
        created_by_key_id: UUID | None = None,
        include_unowned: bool = True,
    ) -> list[WebhookEndpointModel]:
        """List webhook endpoints for a tenant.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            is_active: Optional filter by active status
            created_by_key_id: Optional filter for ownership (returns endpoints
                created by this key)
            include_unowned: If True and created_by_key_id is set, also
                includes endpoints with no ownership (created_by_key_id is NULL).

        Returns:
            List of webhook endpoints
        """
        from sqlalchemy import or_

        query = select(WebhookEndpointModel).where(
            WebhookEndpointModel.tenant_id == tenant_id
        )
        if is_active is not None:
            query = query.where(WebhookEndpointModel.is_active == is_active)

        # Ownership filter - applied at SQL level for consistency
        if created_by_key_id is not None:
            if include_unowned:
                query = query.where(
                    or_(
                        WebhookEndpointModel.created_by_key_id == created_by_key_id,
                        WebhookEndpointModel.created_by_key_id.is_(None),
                    )
                )
            else:
                query = query.where(
                    WebhookEndpointModel.created_by_key_id == created_by_key_id
                )

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
            # Replace junction rows atomically
            from sqlalchemy import delete as sa_delete

            await db.execute(
                sa_delete(WebhookEndpointEvent).where(
                    WebhookEndpointEvent.endpoint_id == endpoint_id
                )
            )
            for event_type in events:
                db.add(
                    WebhookEndpointEvent(endpoint_id=endpoint.id, event_type=event_type)
                )

        if description is not None:
            endpoint.description = description

        if is_active is not None:
            endpoint.is_active = is_active
            # When re-enabling, clear auto-disable state
            if is_active:
                endpoint.disabled_reason = None
                endpoint.consecutive_failures = 0

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

        # Check if endpoint has any deliveries (preserve audit trail)
        has_deliveries = await db.scalar(
            select(WebhookDeliveryModel.id)
            .where(WebhookDeliveryModel.endpoint_id == endpoint_id)
            .limit(1)
        )
        if has_deliveries:
            raise ValueError(
                "Cannot delete endpoint with delivery history. "
                "Deactivate the endpoint instead to preserve audit trail."
            )

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
        cursor: str | None = None,
        sort: Literal["created_desc", "created_asc"] = "created_desc",
    ) -> tuple[list[WebhookDeliveryModel], bool]:
        """List delivery attempts for an endpoint with cursor-based pagination.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            tenant_id: Tenant UUID for isolation check
            status: Optional filter by status
            limit: Max results to return
            cursor: Pagination cursor (format: created_at_iso:delivery_id)
            sort: Sort order for created_at

        Returns:
            Tuple of (deliveries list, has_more)
        """
        # Verify endpoint belongs to tenant
        endpoint = await self.get_endpoint(db, endpoint_id, tenant_id)
        if endpoint is None:
            return [], False

        # Build query
        query = select(WebhookDeliveryModel).where(
            WebhookDeliveryModel.endpoint_id == endpoint_id
        )
        if status is not None:
            query = query.where(WebhookDeliveryModel.status == status)

        # Apply cursor filter
        if cursor:
            cursor_created_at, cursor_id = self._decode_delivery_cursor(cursor)
            if sort == "created_asc":
                # Get deliveries created after cursor OR same time but with larger ID
                query = query.where(
                    (WebhookDeliveryModel.created_at > cursor_created_at)
                    | (
                        (WebhookDeliveryModel.created_at == cursor_created_at)
                        & (WebhookDeliveryModel.id > cursor_id)
                    )
                )
            else:
                # Get deliveries created before cursor OR same time but with smaller ID
                query = query.where(
                    (WebhookDeliveryModel.created_at < cursor_created_at)
                    | (
                        (WebhookDeliveryModel.created_at == cursor_created_at)
                        & (WebhookDeliveryModel.id < cursor_id)
                    )
                )

        # Fetch limit + 1 to determine has_more
        if sort == "created_asc":
            query = query.order_by(
                WebhookDeliveryModel.created_at.asc(),
                WebhookDeliveryModel.id.asc(),
            )
        else:
            query = query.order_by(
                WebhookDeliveryModel.created_at.desc(),
                WebhookDeliveryModel.id.desc(),
            )
        query = query.limit(limit + 1)

        result = await db.execute(query)
        deliveries = list(result.scalars().all())

        has_more = len(deliveries) > limit
        if has_more:
            deliveries = deliveries[:limit]

        return deliveries, has_more

    def encode_delivery_cursor(self, delivery: WebhookDeliveryModel) -> str:
        """Encode a cursor from a delivery's created_at and id."""
        return f"{delivery.created_at.isoformat()}:{delivery.id}"

    def _decode_delivery_cursor(self, cursor: str) -> tuple[datetime, UUID]:
        """Decode a cursor into created_at and id.

        Raises:
            ValueError: If the cursor format is invalid.
        """
        parts = cursor.rsplit(":", 1)
        if len(parts) != 2:
            raise ValueError("Invalid cursor format")
        created_at = datetime.fromisoformat(parts[0])
        delivery_id = UUID(parts[1])
        return created_at, delivery_id

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
        # Match endpoints where junction table contains the event type or wildcard
        from sqlalchemy import exists

        has_event = exists(
            select(WebhookEndpointEvent.endpoint_id).where(
                WebhookEndpointEvent.endpoint_id == WebhookEndpointModel.id,
                WebhookEndpointEvent.event_type == event_type,
            )
        )
        has_wildcard = exists(
            select(WebhookEndpointEvent.endpoint_id).where(
                WebhookEndpointEvent.endpoint_id == WebhookEndpointModel.id,
                WebhookEndpointEvent.event_type == "*",
            )
        )
        query = select(WebhookEndpointModel).where(
            and_(
                WebhookEndpointModel.tenant_id == tenant_id,
                WebhookEndpointModel.is_active == True,  # noqa: E712
                has_event | has_wildcard,
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

    # =========================================================================
    # Authorized Methods (M45 Phase 4)
    # =========================================================================

    async def get_endpoint_authorized(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        principal: Principal,
        security_manager: SecurityManager,
    ) -> WebhookEndpointModel | None:
        """Get webhook endpoint with authorization check.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            principal: Authenticated principal
            security_manager: SecurityManager instance

        Returns:
            Webhook endpoint if found and accessible, None otherwise

        Raises:
            AuthorizationError: If principal lacks WEBHOOK_READ permission
        """
        security_manager.require_permission(principal, Permission.WEBHOOK_READ)

        endpoint = await self.get_endpoint(db, endpoint_id, principal.tenant_id)
        if endpoint is None:
            return None

        # Check ownership for non-admin
        if not principal.is_admin:
            if (
                endpoint.created_by_key_id
                and endpoint.created_by_key_id != principal.id
            ):
                return None  # Return None to map to 404 (anti-enumeration)

        return endpoint

    async def list_endpoints_authorized(
        self,
        db: AsyncSession,
        principal: Principal,
        security_manager: SecurityManager,
        is_active: bool | None = None,
    ) -> list[WebhookEndpointModel]:
        """List webhook endpoints with authorization check.

        Non-admin principals only see endpoints they created (ownership filtering
        applied at SQL level for consistency).

        Args:
            db: Database session
            principal: Authenticated principal
            security_manager: SecurityManager instance
            is_active: Optional filter by active status

        Returns:
            List of webhook endpoints

        Raises:
            AuthorizationError: If principal lacks WEBHOOK_READ permission
        """
        security_manager.require_permission(principal, Permission.WEBHOOK_READ)

        # Apply ownership filter at SQL level
        created_by_key_id = None if principal.is_admin else principal.id

        return await self.list_endpoints(
            db,
            principal.tenant_id,
            is_active=is_active,
            created_by_key_id=created_by_key_id,
            include_unowned=False,  # Strict ownership enforcement
        )

    async def update_endpoint_authorized(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        principal: Principal,
        security_manager: SecurityManager,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> WebhookEndpointModel | None:
        """Update webhook endpoint with authorization check.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            principal: Authenticated principal
            security_manager: SecurityManager instance
            url: New URL (optional)
            events: New events list (optional)
            description: New description (optional)
            is_active: New active status (optional)

        Returns:
            Updated endpoint or None if not found

        Raises:
            AuthorizationError: If principal lacks WEBHOOK_UPDATE permission
            ResourceNotFoundError: If endpoint not found or not accessible
        """
        security_manager.require_permission(principal, Permission.WEBHOOK_UPDATE)

        endpoint = await self.get_endpoint(db, endpoint_id, principal.tenant_id)
        if endpoint is None:
            raise ResourceNotFoundError("webhook_endpoint", endpoint_id)

        # Check ownership for non-admin
        if not principal.is_admin:
            if (
                endpoint.created_by_key_id
                and endpoint.created_by_key_id != principal.id
            ):
                raise ResourceNotFoundError("webhook_endpoint", endpoint_id)

        return await self.update_endpoint(
            db, endpoint_id, principal.tenant_id, url, events, description, is_active
        )

    async def delete_endpoint_authorized(
        self,
        db: AsyncSession,
        endpoint_id: UUID,
        principal: Principal,
        security_manager: SecurityManager,
    ) -> bool:
        """Delete webhook endpoint with authorization check.

        Args:
            db: Database session
            endpoint_id: Endpoint UUID
            principal: Authenticated principal
            security_manager: SecurityManager instance

        Returns:
            True if deleted, False if not found

        Raises:
            AuthorizationError: If principal lacks WEBHOOK_DELETE permission
            ResourceNotFoundError: If endpoint not found or not accessible
        """
        security_manager.require_permission(principal, Permission.WEBHOOK_DELETE)

        endpoint = await self.get_endpoint(db, endpoint_id, principal.tenant_id)
        if endpoint is None:
            raise ResourceNotFoundError("webhook_endpoint", endpoint_id)

        # Check ownership for non-admin
        if not principal.is_admin:
            if (
                endpoint.created_by_key_id
                and endpoint.created_by_key_id != principal.id
            ):
                raise ResourceNotFoundError("webhook_endpoint", endpoint_id)

        return await self.delete_endpoint(db, endpoint_id, principal.tenant_id)
