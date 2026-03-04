"""Audit query service for reading audit logs.

This service handles all read operations for audit logs. Write operations
are handled by dalston.common.audit.AuditService.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import AuditLogModel


@dataclass
class AuditEventDTO:
    """Audit event data transfer object."""

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


@dataclass
class AuditListResult:
    """Result of audit log query with pagination."""

    events: list[AuditEventDTO]
    cursor: str | None
    has_more: bool


class AuditQueryService:
    """Service for querying audit logs.

    This service is separate from the write-side AuditService to maintain
    clear separation of concerns between read and write operations.
    """

    async def list_events(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        actor_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        correlation_id: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
        sort: Literal["timestamp_desc", "timestamp_asc"] = "timestamp_desc",
    ) -> AuditListResult:
        """List audit events with filtering and cursor-based pagination.

        Args:
            db: Database session
            tenant_id: Tenant ID to filter by
            resource_type: Filter by resource type (e.g., job, session)
            resource_id: Filter by resource ID
            action: Filter by action (e.g., job.created)
            actor_id: Filter by actor ID (e.g., API key prefix)
            start_time: Start of time range (inclusive)
            end_time: End of time range (exclusive)
            correlation_id: Filter by correlation ID
            limit: Maximum number of results
            cursor: Pagination cursor (event ID)
            sort: Sort order

        Returns:
            AuditListResult with events, cursor, and has_more flag

        Raises:
            ValueError: If cursor format is invalid
        """
        query = select(AuditLogModel).where(AuditLogModel.tenant_id == tenant_id)

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
            cursor_id = self._decode_cursor(cursor)
            if sort == "timestamp_asc":
                query = query.where(AuditLogModel.id > cursor_id)
            else:
                query = query.where(AuditLogModel.id < cursor_id)

        # Apply sorting and limit
        if sort == "timestamp_asc":
            query = query.order_by(AuditLogModel.id.asc())
        else:
            query = query.order_by(AuditLogModel.id.desc())

        query = query.limit(limit + 1)
        result = await db.execute(query)
        orm_events = list(result.scalars().all())

        has_more = len(orm_events) > limit
        if has_more:
            orm_events = orm_events[:limit]

        next_cursor = str(orm_events[-1].id) if orm_events and has_more else None

        events = [self._to_dto(e) for e in orm_events]

        return AuditListResult(
            events=events,
            cursor=next_cursor,
            has_more=has_more,
        )

    async def get_resource_trail(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        resource_type: str,
        resource_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> AuditListResult:
        """Get complete audit trail for a specific resource.

        Args:
            db: Database session
            tenant_id: Tenant ID to filter by
            resource_type: Resource type (e.g., job, session)
            resource_id: Resource ID
            limit: Maximum number of results
            cursor: Pagination cursor (event ID)

        Returns:
            AuditListResult with events, cursor, and has_more flag

        Raises:
            ValueError: If cursor format is invalid
        """
        query = (
            select(AuditLogModel)
            .where(AuditLogModel.tenant_id == tenant_id)
            .where(AuditLogModel.resource_type == resource_type)
            .where(AuditLogModel.resource_id == resource_id)
        )

        # Apply cursor filter
        if cursor:
            cursor_id = self._decode_cursor(cursor)
            query = query.where(AuditLogModel.id < cursor_id)

        query = query.order_by(AuditLogModel.id.desc()).limit(limit + 1)
        result = await db.execute(query)
        orm_events = list(result.scalars().all())

        has_more = len(orm_events) > limit
        if has_more:
            orm_events = orm_events[:limit]

        next_cursor = str(orm_events[-1].id) if orm_events and has_more else None

        events = [self._to_dto(e) for e in orm_events]

        return AuditListResult(
            events=events,
            cursor=next_cursor,
            has_more=has_more,
        )

    def _to_dto(self, audit: AuditLogModel) -> AuditEventDTO:
        """Convert ORM entity to DTO."""
        return AuditEventDTO(
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

    def _decode_cursor(self, cursor: str) -> int:
        """Decode a pagination cursor to event ID.

        Raises:
            ValueError: If cursor format is invalid
        """
        try:
            return int(cursor)
        except ValueError:
            raise ValueError("Invalid cursor format") from None
