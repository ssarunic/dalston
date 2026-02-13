"""Audit log API endpoints.

GET /v1/audit                           List audit events (with filters)
GET /v1/audit/resources/{type}/{id}     Get audit trail for a resource
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import AuditLogModel
from dalston.gateway.dependencies import RequireAdmin, get_db

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
    total: int
    limit: int
    offset: int
    has_more: bool


def _audit_to_response(audit: AuditLogModel) -> AuditEventResponse:
    """Convert AuditLogModel to response."""
    return AuditEventResponse(
        id=audit.id,
        timestamp=audit.timestamp,
        correlation_id=audit.correlation_id,
        tenant_id=audit.tenant_id,
        actor_type=audit.actor_type,
        actor_id=audit.actor_id,
        action=audit.action,
        resource_type=audit.resource_type,
        resource_id=audit.resource_id,
        detail=audit.detail,
        ip_address=str(audit.ip_address) if audit.ip_address else None,
        user_agent=audit.user_agent,
    )


@router.get(
    "",
    response_model=AuditListResponse,
    summary="List audit events",
    description="List audit events with optional filters. Requires admin scope.",
)
async def list_audit_events(
    api_key: RequireAdmin,
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
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    """List audit events with filtering and pagination.

    Events are filtered to the tenant of the authenticated API key.
    """
    # Build query with filters
    query = select(AuditLogModel).where(AuditLogModel.tenant_id == api_key.tenant_id)

    if resource_type:
        query = query.where(AuditLogModel.resource_type == resource_type)
    if resource_id:
        query = query.where(AuditLogModel.resource_id == resource_id)
    if action:
        query = query.where(AuditLogModel.action == action)
    if actor_id:
        query = query.where(AuditLogModel.actor_id == actor_id)
    if correlation_id:
        query = query.where(AuditLogModel.correlation_id == correlation_id)
    if start_time:
        query = query.where(AuditLogModel.timestamp >= start_time)
    if end_time:
        query = query.where(AuditLogModel.timestamp < end_time)

    # Get total count (with +1 for has_more)
    count_query = query.order_by(AuditLogModel.timestamp.desc())
    result = await db.execute(count_query.offset(offset).limit(limit + 1))
    events = list(result.scalars().all())

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    # Count total (approximate for performance)
    total = offset + len(events) + (1 if has_more else 0)

    return AuditListResponse(
        events=[_audit_to_response(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
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
    api_key: RequireAdmin,
    limit: Annotated[int, Query(ge=1, le=100, description="Max results")] = 25,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    """Get all audit events for a specific resource.

    This is useful for compliance audits to see the complete history
    of actions on a job, session, or other resource.
    """
    query = (
        select(AuditLogModel)
        .where(AuditLogModel.tenant_id == api_key.tenant_id)
        .where(AuditLogModel.resource_type == resource_type)
        .where(AuditLogModel.resource_id == resource_id)
        .order_by(AuditLogModel.timestamp.desc())
    )

    result = await db.execute(query.offset(offset).limit(limit + 1))
    events = list(result.scalars().all())

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    total = offset + len(events) + (1 if has_more else 0)

    return AuditListResponse(
        events=[_audit_to_response(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
        has_more=has_more,
    )
