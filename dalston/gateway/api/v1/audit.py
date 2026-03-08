"""Audit log API endpoints.

GET /v1/audit                           List audit events (with filters)
GET /v1/audit/resources/{type}/{id}     Get audit trail for a resource
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.gateway.error_codes import Err
from dalston.gateway.dependencies import (
    get_audit_query_service,
    get_db,
    get_principal,
    get_security_manager,
)
from dalston.gateway.security.permissions import Permission
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.audit_query import AuditEventDTO, AuditQueryService

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    """Response for a single audit event."""

    id: int
    timestamp: datetime
    correlation_id: str | None
    tenant_id: UUID | None
    actor_type: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    detail: dict | None
    ip_address: str | None
    user_agent: str | None


class AuditListResponse(BaseModel):
    """Response for listing audit events."""

    events: list[AuditEventResponse]
    cursor: str | None
    has_more: bool


def _dto_to_response(dto: AuditEventDTO) -> AuditEventResponse:
    """Convert AuditEventDTO to response."""
    return AuditEventResponse(
        id=dto.id,
        timestamp=dto.timestamp,
        correlation_id=dto.correlation_id,
        tenant_id=dto.tenant_id,
        actor_type=dto.actor_type,
        actor_id=dto.actor_id,
        action=dto.action,
        resource_type=dto.resource_type,
        resource_id=dto.resource_id,
        detail=dto.detail,
        ip_address=dto.ip_address,
        user_agent=dto.user_agent,
    )


@router.get(
    "",
    response_model=AuditListResponse,
    summary="List audit events",
    description="List audit events with optional filters. Requires admin scope.",
)
async def list_audit_events(
    principal: Annotated[Principal, Depends(get_principal)],
    resource_type: Annotated[
        str | None, Query(description="Filter by resource type (e.g., job, session)")
    ] = None,
    resource_id: Annotated[
        str | None, Query(description="Filter by resource ID")
    ] = None,
    action: Annotated[
        str | None, Query(description="Filter by action (e.g., job.created)")
    ] = None,
    actor_id: Annotated[
        str | None, Query(description="Filter by actor ID (e.g., API key prefix)")
    ] = None,
    start_time: Annotated[
        datetime | None, Query(description="Start of time range (inclusive)")
    ] = None,
    end_time: Annotated[
        datetime | None, Query(description="End of time range (exclusive)")
    ] = None,
    correlation_id: Annotated[
        str | None, Query(description="Filter by correlation ID")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 25,
    cursor: Annotated[
        str | None, Query(description="Cursor for pagination (last event ID)")
    ] = None,
    sort: Annotated[
        Literal["timestamp_desc", "timestamp_asc"],
        Query(description="Sort order by timestamp"),
    ] = "timestamp_desc",
    db: AsyncSession = Depends(get_db),
    audit_service: AuditQueryService = Depends(get_audit_query_service),
) -> AuditListResponse:
    """List audit events with filtering and cursor-based pagination.

    Events are filtered to the tenant of the authenticated principal.
    Pass the cursor from the previous response to fetch the next page.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.AUDIT_READ)

    try:
        result = await audit_service.list_events(
            db,
            principal.tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            actor_id=actor_id,
            start_time=start_time,
            end_time=end_time,
            correlation_id=correlation_id,
            limit=limit,
            cursor=cursor,
            sort=sort,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=Err.INVALID_CURSOR_FORMAT) from None

    return AuditListResponse(
        events=[_dto_to_response(e) for e in result.events],
        cursor=result.cursor,
        has_more=result.has_more,
    )


@router.get(
    "/resources/{resource_type}/{resource_id}",
    response_model=AuditListResponse,
    summary="Get resource audit trail",
    description="Get the complete audit trail for a specific resource.",
)
async def get_resource_audit_trail(
    resource_type: str,
    resource_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 25,
    cursor: Annotated[
        str | None, Query(description="Cursor for pagination (last event ID)")
    ] = None,
    db: AsyncSession = Depends(get_db),
    audit_service: AuditQueryService = Depends(get_audit_query_service),
) -> AuditListResponse:
    """Get all audit events for a specific resource.

    This is useful for compliance audits to see the complete history
    of actions on a job, session, or other resource.
    """
    security_manager = get_security_manager()
    security_manager.require_permission(principal, Permission.AUDIT_READ)

    try:
        result = await audit_service.get_resource_trail(
            db,
            principal.tenant_id,
            resource_type,
            resource_id,
            limit=limit,
            cursor=cursor,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=Err.INVALID_CURSOR_FORMAT) from None

    return AuditListResponse(
        events=[_dto_to_response(e) for e in result.events],
        cursor=result.cursor,
        has_more=result.has_more,
    )
