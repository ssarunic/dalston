"""Artifact lifecycle management service."""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import text, update
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

        # Update all artifacts for this owner
        # Set available_at and compute purge_after = available_at + ttl_seconds
        stmt = (
            update(ArtifactObjectModel)
            .where(ArtifactObjectModel.owner_type == owner_type.value)
            .where(ArtifactObjectModel.owner_id == owner_id)
            .where(ArtifactObjectModel.purge_after.is_(None))  # Only unprocessed
            .values(
                available_at=available_at,
                # purge_after is computed from ttl_seconds if set
                # This is handled per-row below since we can't use column in values
            )
        )
        result = await db.execute(stmt)
        count = result.rowcount

        # Now update purge_after for artifacts with ttl_seconds
        # purge_after = available_at + ttl_seconds
        # Use raw SQL to compute interval to avoid Python/SQL datetime mixing issues
        # Note: Use CAST() instead of :: to avoid conflict with SQLAlchemy named params
        await db.execute(
            text("""
                UPDATE artifact_objects
                SET purge_after = CAST(:available_at AS TIMESTAMPTZ) + CAST(ttl_seconds || ' seconds' AS INTERVAL)
                WHERE owner_type = :owner_type
                  AND owner_id = :owner_id
                  AND ttl_seconds IS NOT NULL
                  AND purge_after IS NULL
            """),
            {
                "available_at": available_at,
                "owner_type": owner_type.value,
                "owner_id": owner_id,
            },
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
