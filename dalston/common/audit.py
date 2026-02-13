"""Audit logging service with fail-open behavior.

This module provides audit trail functionality for compliance and security tracking.
The service follows fail-open semantics: if audit logging fails, the operation
continues and the error is logged but not propagated.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import AuditLogModel

logger = structlog.get_logger()


class AuditService:
    """Service for immutable audit log entries.

    Follows fail-open semantics: audit failures are logged but do not
    block business operations. This ensures reliability while maintaining
    audit trails for compliance.
    """

    def __init__(
        self,
        db_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ):
        """Initialize AuditService.

        Args:
            db_session_factory: Async context manager factory for database sessions.
                               This allows the service to create independent sessions
                               for audit writes.
        """
        self.db_session_factory = db_session_factory

    async def log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        *,
        tenant_id: UUID | None = None,
        actor_type: str = "system",
        actor_id: str = "unknown",
        detail: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log an audit event.

        This method is fail-open: if the audit write fails, the error is logged
        but not re-raised. This ensures audit logging never blocks operations.

        Args:
            action: Action performed (e.g., "job.created", "transcript.accessed")
            resource_type: Type of resource (e.g., "job", "session", "api_key")
            resource_id: ID of the resource (typically UUID as string)
            tenant_id: Optional tenant UUID
            actor_type: Type of actor (api_key, system, user)
            actor_id: ID of actor (key prefix, system name, etc.)
            detail: Optional additional context as JSON
            correlation_id: Optional request correlation ID
            ip_address: Optional client IP address
            user_agent: Optional client user agent
        """
        try:
            async with self.db_session_factory() as session:
                entry = AuditLogModel(
                    correlation_id=correlation_id,
                    tenant_id=tenant_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    detail=detail,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                session.add(entry)
                await session.commit()

                logger.debug(
                    "audit_event_logged",
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                )
        except Exception:
            # Fail open - log the error but don't re-raise
            logger.error(
                "audit_log_write_failed",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                exc_info=True,
            )

    async def log_job_created(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        retention_policy: str | None = None,
    ) -> None:
        """Log job creation event."""
        detail = {}
        if retention_policy:
            detail["retention_policy"] = retention_policy

        await self.log(
            action="job.created",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_audio_uploaded(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
        file_size: int | None = None,
        audio_duration: float | None = None,
    ) -> None:
        """Log audio upload event."""
        detail = {}
        if file_size is not None:
            detail["file_size"] = file_size
        if audio_duration is not None:
            detail["audio_duration"] = audio_duration

        await self.log(
            action="audio.uploaded",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_transcript_accessed(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log transcript access event."""
        await self.log(
            action="transcript.accessed",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_transcript_exported(
        self,
        job_id: UUID,
        tenant_id: UUID,
        export_format: str,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log transcript export event."""
        await self.log(
            action="transcript.exported",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail={"format": export_format},
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_audio_deleted(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log audio deletion event."""
        await self.log(
            action="audio.deleted",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_job_deleted(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log job deletion event."""
        await self.log(
            action="job.deleted",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_job_purged(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        artifacts_deleted: list[str] | None = None,
    ) -> None:
        """Log job purge event (automated cleanup)."""
        detail = {}
        if artifacts_deleted:
            detail["artifacts_deleted"] = artifacts_deleted

        await self.log(
            action="job.purged",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type="system",
            actor_id="cleanup_worker",
            detail=detail if detail else None,
        )

    async def log_session_started(
        self,
        session_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        """Log realtime session start event."""
        detail = {}
        if worker_id:
            detail["worker_id"] = worker_id

        await self.log(
            action="session.started",
            resource_type="session",
            resource_id=str(session_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_session_ended(
        self,
        session_id: UUID,
        tenant_id: UUID,
        *,
        duration_seconds: float | None = None,
        word_count: int | None = None,
    ) -> None:
        """Log realtime session end event."""
        detail = {}
        if duration_seconds is not None:
            detail["duration_seconds"] = duration_seconds
        if word_count is not None:
            detail["word_count"] = word_count

        await self.log(
            action="session.ended",
            resource_type="session",
            resource_id=str(session_id),
            tenant_id=tenant_id,
            actor_type="system",
            actor_id="session_router",
            detail=detail if detail else None,
        )

    async def log_api_key_created(
        self,
        key_id: UUID,
        tenant_id: UUID,
        key_name: str,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log API key creation event."""
        await self.log(
            action="api_key.created",
            resource_type="api_key",
            resource_id=str(key_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail={"key_name": key_name},
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_api_key_revoked(
        self,
        key_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log API key revocation event."""
        await self.log(
            action="api_key.revoked",
            resource_type="api_key",
            resource_id=str(key_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_retention_policy_created(
        self,
        policy_id: UUID,
        tenant_id: UUID,
        policy_name: str,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log retention policy creation event."""
        await self.log(
            action="retention_policy.created",
            resource_type="retention_policy",
            resource_id=str(policy_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail={"policy_name": policy_name},
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_retention_policy_deleted(
        self,
        policy_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log retention policy deletion event."""
        await self.log(
            action="retention_policy.deleted",
            resource_type="retention_policy",
            resource_id=str(policy_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )
