"""Webhook management API endpoints.

POST   /v1/webhooks                                    - Create webhook endpoint
GET    /v1/webhooks                                    - List webhook endpoints
GET    /v1/webhooks/{endpoint_id}                      - Get webhook endpoint
PATCH  /v1/webhooks/{endpoint_id}                      - Update webhook endpoint
DELETE /v1/webhooks/{endpoint_id}                      - Delete webhook endpoint
POST   /v1/webhooks/{endpoint_id}/rotate-secret        - Rotate signing secret
GET    /v1/webhooks/{endpoint_id}/deliveries           - List deliveries
POST   /v1/webhooks/{endpoint_id}/deliveries/{id}/retry - Retry delivery
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.gateway.dependencies import RequireWebhooks, get_db
from dalston.gateway.services.webhook import WebhookValidationError
from dalston.gateway.services.webhook_endpoints import (
    ALLOWED_EVENTS,
    WebhookEndpointService,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Service singleton
_webhook_endpoint_service: WebhookEndpointService | None = None


def get_webhook_endpoint_service() -> WebhookEndpointService:
    """Get WebhookEndpointService instance (singleton)."""
    global _webhook_endpoint_service
    if _webhook_endpoint_service is None:
        _webhook_endpoint_service = WebhookEndpointService()
    return _webhook_endpoint_service


# Request models


class CreateWebhookRequest(BaseModel):
    """Request body for creating a webhook endpoint."""

    url: HttpUrl = Field(description="Webhook callback URL (must be HTTPS in production)")
    events: list[str] = Field(
        description=f"Event types to subscribe to. Allowed: {', '.join(sorted(ALLOWED_EVENTS))}"
    )
    description: str | None = Field(
        default=None, max_length=255, description="Human-readable description"
    )


class UpdateWebhookRequest(BaseModel):
    """Request body for updating a webhook endpoint."""

    url: HttpUrl | None = Field(default=None, description="New callback URL")
    events: list[str] | None = Field(default=None, description="New event subscriptions")
    description: str | None = Field(default=None, max_length=255, description="New description")
    is_active: bool | None = Field(default=None, description="Enable/disable endpoint")


# Response models


class WebhookEndpointResponse(BaseModel):
    """Webhook endpoint details (without signing secret)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    events: list[str]
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    """Webhook endpoint with signing secret (only returned on create/rotate)."""

    signing_secret: str = Field(
        description="HMAC signing secret. Store securely - only shown once!"
    )


class WebhookEndpointListResponse(BaseModel):
    """List of webhook endpoints."""

    endpoints: list[WebhookEndpointResponse]


class WebhookDeliveryResponse(BaseModel):
    """Webhook delivery attempt details."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    endpoint_id: UUID | None
    job_id: UUID | None
    event_type: str
    status: str
    attempts: int
    last_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime


class DeliveryListResponse(BaseModel):
    """Paginated list of webhook deliveries."""

    deliveries: list[WebhookDeliveryResponse]
    total: int
    limit: int
    offset: int


# Endpoints


@router.post(
    "",
    response_model=WebhookEndpointCreatedResponse,
    status_code=201,
    summary="Create webhook endpoint",
    description="Register a new webhook endpoint. The signing secret is only returned once.",
)
async def create_webhook_endpoint(
    request: CreateWebhookRequest,
    api_key: RequireWebhooks,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointCreatedResponse:
    """Create a new webhook endpoint."""
    try:
        endpoint, raw_secret = await service.create_endpoint(
            db=db,
            tenant_id=api_key.tenant_id,
            url=str(request.url),
            events=request.events,
            description=request.description,
        )
    except WebhookValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return WebhookEndpointCreatedResponse(
        id=endpoint.id,
        url=endpoint.url,
        events=endpoint.events,
        description=endpoint.description,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        signing_secret=raw_secret,
    )


@router.get(
    "",
    response_model=WebhookEndpointListResponse,
    summary="List webhook endpoints",
    description="List all webhook endpoints for your tenant.",
)
async def list_webhook_endpoints(
    api_key: RequireWebhooks,
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointListResponse:
    """List webhook endpoints."""
    endpoints = await service.list_endpoints(
        db=db,
        tenant_id=api_key.tenant_id,
        is_active=is_active,
    )
    return WebhookEndpointListResponse(
        endpoints=[
            WebhookEndpointResponse(
                id=e.id,
                url=e.url,
                events=e.events,
                description=e.description,
                is_active=e.is_active,
                created_at=e.created_at,
                updated_at=e.updated_at,
            )
            for e in endpoints
        ]
    )


@router.get(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    summary="Get webhook endpoint",
    description="Get details of a specific webhook endpoint.",
)
async def get_webhook_endpoint(
    endpoint_id: UUID,
    api_key: RequireWebhooks,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointResponse:
    """Get a webhook endpoint by ID."""
    endpoint = await service.get_endpoint(
        db=db,
        endpoint_id=endpoint_id,
        tenant_id=api_key.tenant_id,
    )
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    return WebhookEndpointResponse(
        id=endpoint.id,
        url=endpoint.url,
        events=endpoint.events,
        description=endpoint.description,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


@router.patch(
    "/{endpoint_id}",
    response_model=WebhookEndpointResponse,
    summary="Update webhook endpoint",
    description="Update a webhook endpoint's URL, events, description, or active status.",
)
async def update_webhook_endpoint(
    endpoint_id: UUID,
    request: UpdateWebhookRequest,
    api_key: RequireWebhooks,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointResponse:
    """Update a webhook endpoint."""
    try:
        endpoint = await service.update_endpoint(
            db=db,
            endpoint_id=endpoint_id,
            tenant_id=api_key.tenant_id,
            url=str(request.url) if request.url else None,
            events=request.events,
            description=request.description,
            is_active=request.is_active,
        )
    except WebhookValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    return WebhookEndpointResponse(
        id=endpoint.id,
        url=endpoint.url,
        events=endpoint.events,
        description=endpoint.description,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


@router.delete(
    "/{endpoint_id}",
    status_code=204,
    summary="Delete webhook endpoint",
    description="Delete a webhook endpoint and all its delivery history.",
)
async def delete_webhook_endpoint(
    endpoint_id: UUID,
    api_key: RequireWebhooks,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> None:
    """Delete a webhook endpoint."""
    deleted = await service.delete_endpoint(
        db=db,
        endpoint_id=endpoint_id,
        tenant_id=api_key.tenant_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")


@router.post(
    "/{endpoint_id}/rotate-secret",
    response_model=WebhookEndpointCreatedResponse,
    summary="Rotate signing secret",
    description="Generate a new signing secret. The old secret becomes invalid immediately.",
)
async def rotate_webhook_secret(
    endpoint_id: UUID,
    api_key: RequireWebhooks,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookEndpointCreatedResponse:
    """Rotate the signing secret for a webhook endpoint."""
    result = await service.rotate_secret(
        db=db,
        endpoint_id=endpoint_id,
        tenant_id=api_key.tenant_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    endpoint, raw_secret = result
    return WebhookEndpointCreatedResponse(
        id=endpoint.id,
        url=endpoint.url,
        events=endpoint.events,
        description=endpoint.description,
        is_active=endpoint.is_active,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
        signing_secret=raw_secret,
    )


@router.get(
    "/{endpoint_id}/deliveries",
    response_model=DeliveryListResponse,
    summary="List deliveries",
    description="List delivery attempts for a webhook endpoint.",
)
async def list_webhook_deliveries(
    endpoint_id: UUID,
    api_key: RequireWebhooks,
    status: Annotated[str | None, Query(description="Filter by status")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> DeliveryListResponse:
    """List delivery attempts for a webhook endpoint."""
    deliveries, total = await service.list_deliveries(
        db=db,
        endpoint_id=endpoint_id,
        tenant_id=api_key.tenant_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return DeliveryListResponse(
        deliveries=[
            WebhookDeliveryResponse(
                id=d.id,
                endpoint_id=d.endpoint_id,
                job_id=d.job_id,
                event_type=d.event_type,
                status=d.status,
                attempts=d.attempts,
                last_attempt_at=d.last_attempt_at,
                last_status_code=d.last_status_code,
                last_error=d.last_error,
                created_at=d.created_at,
            )
            for d in deliveries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{endpoint_id}/deliveries/{delivery_id}/retry",
    response_model=WebhookDeliveryResponse,
    summary="Retry delivery",
    description="Retry a failed webhook delivery.",
)
async def retry_webhook_delivery(
    endpoint_id: UUID,
    delivery_id: UUID,
    api_key: RequireWebhooks,
    db: AsyncSession = Depends(get_db),
    service: WebhookEndpointService = Depends(get_webhook_endpoint_service),
) -> WebhookDeliveryResponse:
    """Retry a failed webhook delivery."""
    try:
        delivery = await service.retry_delivery(
            db=db,
            endpoint_id=endpoint_id,
            delivery_id=delivery_id,
            tenant_id=api_key.tenant_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")

    return WebhookDeliveryResponse(
        id=delivery.id,
        endpoint_id=delivery.endpoint_id,
        job_id=delivery.job_id,
        event_type=delivery.event_type,
        status=delivery.status,
        attempts=delivery.attempts,
        last_attempt_at=delivery.last_attempt_at,
        last_status_code=delivery.last_status_code,
        last_error=delivery.last_error,
        created_at=delivery.created_at,
    )
