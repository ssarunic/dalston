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
        detail: dict[str, int | float] = {}
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

    async def log_job_renamed(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        old_name: str,
        new_name: str,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log job rename event."""
        await self.log(
            action="job.renamed",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail={"old_name": old_name, "new_name": new_name},
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
        instance: str | None = None,
    ) -> None:
        """Log realtime session start event."""
        detail = {}
        if instance:
            detail["instance"] = instance

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

    async def log_model_downloaded(
        self,
        model_id: str,
        *,
        tenant_id: UUID | None = None,
        source: str | None = None,
        size_bytes: int | None = None,
        download_path: str | None = None,
        actor_type: str = "system",
        actor_id: str = "model_registry",
        correlation_id: str | None = None,
    ) -> None:
        """Log model download event."""
        detail: dict[str, Any] = {}
        if source:
            detail["source"] = source
        if size_bytes is not None:
            detail["size_bytes"] = size_bytes
        if download_path:
            detail["download_path"] = download_path

        await self.log(
            action="model.downloaded",
            resource_type="model",
            resource_id=model_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
        )

    async def log_model_removed(
        self,
        model_id: str,
        *,
        tenant_id: UUID | None = None,
        download_path: str | None = None,
        actor_type: str = "system",
        actor_id: str = "model_registry",
        correlation_id: str | None = None,
    ) -> None:
        """Log model removal event."""
        detail: dict[str, Any] = {}
        if download_path:
            detail["download_path"] = download_path

        await self.log(
            action="model.removed",
            resource_type="model",
            resource_id=model_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
        )

    async def log_model_download_failed(
        self,
        model_id: str,
        *,
        tenant_id: UUID | None = None,
        error: str | None = None,
        actor_type: str = "system",
        actor_id: str = "model_registry",
        correlation_id: str | None = None,
    ) -> None:
        """Log model download failure event."""
        detail: dict[str, Any] = {}
        if error:
            detail["error"] = error

        await self.log(
            action="model.download_failed",
            resource_type="model",
            resource_id=model_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
        )

    async def log_model_deleted_from_registry(
        self,
        model_id: str,
        *,
        tenant_id: UUID | None = None,
        download_path: str | None = None,
        actor_type: str = "system",
        actor_id: str = "model_registry",
        correlation_id: str | None = None,
    ) -> None:
        """Log model deletion from registry event."""
        detail: dict[str, Any] = {}
        if download_path:
            detail["download_path"] = download_path

        await self.log(
            action="model.deleted_from_registry",
            resource_type="model",
            resource_id=model_id,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            detail=detail if detail else None,
            correlation_id=correlation_id,
        )

    async def log_job_cancel_requested(
        self,
        job_id: UUID,
        tenant_id: UUID,
        *,
        actor_type: str = "api_key",
        actor_id: str = "unknown",
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log job cancellation request event."""
        await self.log(
            action="job.cancel_requested",
            resource_type="job",
            resource_id=str(job_id),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_permission_denied(
        self,
        principal_id: UUID,
        permission: str,
        resource_type: str,
        resource_id: str,
        *,
        tenant_id: UUID | None = None,
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log permission denied event for security monitoring."""
        await self.log(
            action="permission.denied",
            resource_type=resource_type,
            resource_id=resource_id,
            tenant_id=tenant_id,
            actor_type="api_key",
            actor_id=str(principal_id),
            detail={"required_permission": permission},
            correlation_id=correlation_id,
            ip_address=ip_address,
        )

    async def log_auth_failure(
        self,
        reason: str,
        *,
        key_prefix: str | None = None,
        correlation_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Log authentication failure for security monitoring."""
        await self.log(
            action="auth.failed",
            resource_type="api_key",
            resource_id=key_prefix or "unknown",
            actor_type="anonymous",
            actor_id="unknown",
            detail={"reason": reason},
            correlation_id=correlation_id,
            ip_address=ip_address,
        )
