"""Artifact lifecycle management service."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.models import ArtifactOwnerType
from dalston.db.models import ArtifactObjectModel

logger = structlog.get_logger()


class ArtifactService:
    """Service for managing artifact lifecycle and retention."""

    async def mark_owner_artifacts_available(
        self,
        db: AsyncSession,
        owner_type: ArtifactOwnerType,
        owner_id: UUID,
        available_at: datetime | None = None,
    ) -> int:
        """Mark all artifacts for an owner as available and compute purge_after.

        This should be called when a job or session completes successfully,
        allowing the retention system to know when to purge artifacts.

        Args:
            db: Database session
            owner_type: Type of owner (job or session)
            owner_id: UUID of the owning job or session
            available_at: When artifacts became available (defaults to now)

        Returns:
            Number of artifacts updated
        """
        if available_at is None:
            available_at = datetime.now(UTC)

        # Update available_at for all unprocessed artifacts for this owner
        stmt = (
            update(ArtifactObjectModel)
            .where(ArtifactObjectModel.owner_type == owner_type.value)
            .where(ArtifactObjectModel.owner_id == owner_id)
            .where(ArtifactObjectModel.purge_after.is_(None))
            .values(available_at=available_at)
        )
        result = await db.execute(stmt)
        count = result.rowcount

        # Compute purge_after in Python to avoid dialect-specific INTERVAL arithmetic.
        # Load only artifacts with a TTL that still need purge_after set.
        artifacts_result = await db.execute(
            select(ArtifactObjectModel).where(
                ArtifactObjectModel.owner_type == owner_type.value,
                ArtifactObjectModel.owner_id == owner_id,
                ArtifactObjectModel.ttl_seconds.is_not(None),
                ArtifactObjectModel.purge_after.is_(None),
            )
        )
        for artifact in artifacts_result.scalars().all():
            artifact.purge_after = available_at + timedelta(
                seconds=artifact.ttl_seconds
            )

        if count > 0:
            logger.debug(
                "artifacts_marked_available",
                owner_type=owner_type.value,
                owner_id=str(owner_id),
                count=count,
                available_at=available_at.isoformat(),
            )

        return count
