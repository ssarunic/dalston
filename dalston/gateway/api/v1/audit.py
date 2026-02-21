"""Audit log API endpoints.

GET /v1/audit                           List audit events (with filters)
GET /v1/audit/resources/{type}/{id}     Get audit trail for a resource
"""

from datetime import datetime
from typing import Annotated, Literal
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
    cursor: str | None
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
    cursor: Annotated[
        str | None, Query(description="Cursor for pagination (last event ID)")
    ] = None,
    sort: Annotated[
        Literal["timestamp_desc", "timestamp_asc"],
        Query(description="Sort order by timestamp"),
    ] = "timestamp_desc",
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    """List audit events with filtering and cursor-based pagination.

    Events are filtered to the tenant of the authenticated API key.
    Pass the cursor from the previous response to fetch the next page.
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

    # Apply cursor filter
    if cursor:
        try:
            cursor_id = int(cursor)
            if sort == "timestamp_asc":
                query = query.where(AuditLogModel.id > cursor_id)
            else:
                query = query.where(AuditLogModel.id < cursor_id)
        except ValueError:
            pass  # Invalid cursor, ignore

    # Fetch limit + 1 to determine has_more
    if sort == "timestamp_asc":
        query = query.order_by(AuditLogModel.id.asc())
    else:
        query = query.order_by(AuditLogModel.id.desc())
    query = query.limit(limit + 1)
    result = await db.execute(query)
    events = list(result.scalars().all())

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    # Next cursor is the ID of the last event
    next_cursor = str(events[-1].id) if events and has_more else None

    return AuditListResponse(
        events=[_audit_to_response(e) for e in events],
        cursor=next_cursor,
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
    cursor: Annotated[
        str | None, Query(description="Cursor for pagination (last event ID)")
    ] = None,
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
    )

    # Apply cursor filter
    if cursor:
        try:
            cursor_id = int(cursor)
            query = query.where(AuditLogModel.id < cursor_id)
        except ValueError:
            pass  # Invalid cursor, ignore

    query = query.order_by(AuditLogModel.id.desc()).limit(limit + 1)
    result = await db.execute(query)
    events = list(result.scalars().all())

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    next_cursor = str(events[-1].id) if events and has_more else None

    return AuditListResponse(
        events=[_audit_to_response(e) for e in events],
        cursor=next_cursor,
        has_more=has_more,
    )
