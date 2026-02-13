"""Retention policy management service."""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.models import RetentionMode, RetentionScope
from dalston.db.models import JobModel, RealtimeSessionModel, RetentionPolicyModel

# System policy IDs (well-known UUIDs from migration)
SYSTEM_POLICY_DEFAULT = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_POLICY_ZERO_RETENTION = UUID("00000000-0000-0000-0000-000000000002")
SYSTEM_POLICY_KEEP = UUID("00000000-0000-0000-0000-000000000003")


class RetentionPolicyNotFoundError(Exception):
    """Raised when a retention policy is not found."""

    pass


class RetentionPolicyInUseError(Exception):
    """Raised when attempting to delete a policy that is still in use."""

    pass


class RetentionService:
    """Service for retention policy CRUD operations and resolution."""

    async def create_policy(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        name: str,
        mode: RetentionMode,
        hours: int | None = None,
        scope: RetentionScope = RetentionScope.ALL,
        realtime_mode: str = "inherit",
        realtime_hours: int | None = None,
        delete_realtime_on_enhancement: bool = True,
    ) -> RetentionPolicyModel:
        """Create a new tenant retention policy.

        Args:
            db: Database session
            tenant_id: Tenant UUID
            name: Policy name (unique per tenant)
            mode: Retention mode (auto_delete, keep, none)
            hours: Hours to retain (required for auto_delete)
            scope: What to delete (all, audio_only)
            realtime_mode: Mode for realtime sessions (inherit, auto_delete, keep, none)
            realtime_hours: Override hours for realtime sessions
            delete_realtime_on_enhancement: Delete realtime artifacts when enhanced

        Returns:
            Created RetentionPolicyModel

        Raises:
            ValueError: If validation fails
        """
        # Validate mode and hours
        if mode == RetentionMode.AUTO_DELETE and hours is None:
            raise ValueError("hours is required when mode is 'auto_delete'")
        if mode in (RetentionMode.KEEP, RetentionMode.NONE) and hours is not None:
            raise ValueError(f"hours must be null when mode is '{mode.value}'")
        if hours is not None and hours < 1:
            raise ValueError("hours must be at least 1")

        # Check for duplicate name
        existing = await self.get_policy_by_name(db, tenant_id, name)
        if existing:
            raise ValueError(f"Policy with name '{name}' already exists")

        policy = RetentionPolicyModel(
            tenant_id=tenant_id,
            name=name,
            mode=mode.value,
            hours=hours,
            scope=scope.value,
            realtime_mode=realtime_mode,
            realtime_hours=realtime_hours,
            delete_realtime_on_enhancement=delete_realtime_on_enhancement,
            is_system=False,
        )
        db.add(policy)
        await db.commit()
        await db.refresh(policy)
        return policy

    async def list_policies(
        self,
        db: AsyncSession,
        tenant_id: UUID,
    ) -> list[RetentionPolicyModel]:
        """List all retention policies available to a tenant.

        Returns both tenant-specific policies and system policies.

        Args:
            db: Database session
            tenant_id: Tenant UUID

        Returns:
            List of RetentionPolicyModel
        """
        # Fetch tenant policies and system policies
        query = (
            select(RetentionPolicyModel)
            .where(
                or_(
                    RetentionPolicyModel.tenant_id == tenant_id,
                    RetentionPolicyModel.tenant_id.is_(None),  # System policies
                )
            )
            .order_by(
                RetentionPolicyModel.is_system.desc(),  # System policies first
                RetentionPolicyModel.name,
            )
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_policy(
        self,
        db: AsyncSession,
        policy_id: UUID,
        tenant_id: UUID | None = None,
    ) -> RetentionPolicyModel | None:
        """Get a retention policy by ID.

        Args:
            db: Database session
            policy_id: Policy UUID
            tenant_id: Optional tenant UUID for access check

        Returns:
            RetentionPolicyModel or None if not found
        """
        query = select(RetentionPolicyModel).where(RetentionPolicyModel.id == policy_id)

        # Tenant can access their own policies and system policies
        if tenant_id is not None:
            query = query.where(
                or_(
                    RetentionPolicyModel.tenant_id == tenant_id,
                    RetentionPolicyModel.tenant_id.is_(None),
                )
            )

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_policy_by_name(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        name: str,
    ) -> RetentionPolicyModel | None:
        """Get a retention policy by name.

        Searches tenant policies first, then falls back to system policies.

        Args:
            db: Database session
            tenant_id: Tenant UUID
            name: Policy name

        Returns:
            RetentionPolicyModel or None if not found
        """
        # First try tenant policy
        query = select(RetentionPolicyModel).where(
            RetentionPolicyModel.tenant_id == tenant_id,
            RetentionPolicyModel.name == name,
        )
        result = await db.execute(query)
        policy = result.scalar_one_or_none()
        if policy:
            return policy

        # Fall back to system policy
        query = select(RetentionPolicyModel).where(
            RetentionPolicyModel.tenant_id.is_(None),
            RetentionPolicyModel.name == name,
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def delete_policy(
        self,
        db: AsyncSession,
        policy_id: UUID,
        tenant_id: UUID,
    ) -> None:
        """Delete a retention policy.

        Args:
            db: Database session
            policy_id: Policy UUID to delete
            tenant_id: Tenant UUID for ownership check

        Raises:
            RetentionPolicyNotFoundError: If policy not found
            RetentionPolicyInUseError: If policy is in use by jobs/sessions
            ValueError: If attempting to delete a system policy
        """
        policy = await self.get_policy(db, policy_id, tenant_id)
        if policy is None:
            raise RetentionPolicyNotFoundError(f"Policy {policy_id} not found")

        if policy.is_system:
            raise ValueError("Cannot delete system policies")

        if policy.tenant_id != tenant_id:
            raise RetentionPolicyNotFoundError(f"Policy {policy_id} not found")

        # Check if policy is in use by any jobs
        jobs_count = await db.scalar(
            select(func.count())
            .select_from(JobModel)
            .where(JobModel.retention_policy_id == policy_id)
        )
        if jobs_count and jobs_count > 0:
            raise RetentionPolicyInUseError(f"Policy is in use by {jobs_count} job(s)")

        # Check if policy is in use by any realtime sessions
        sessions_count = await db.scalar(
            select(func.count())
            .select_from(RealtimeSessionModel)
            .where(RealtimeSessionModel.retention_policy_id == policy_id)
        )
        if sessions_count and sessions_count > 0:
            raise RetentionPolicyInUseError(
                f"Policy is in use by {sessions_count} session(s)"
            )

        await db.delete(policy)
        await db.commit()

    async def resolve_policy(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        policy_name: str | None = None,
    ) -> RetentionPolicyModel:
        """Resolve a retention policy for a job or session.

        Resolution order:
        1. If policy_name provided: look up by name (tenant -> system)
        2. If not provided: use system 'default' policy

        Args:
            db: Database session
            tenant_id: Tenant UUID
            policy_name: Optional policy name

        Returns:
            Resolved RetentionPolicyModel

        Raises:
            RetentionPolicyNotFoundError: If policy not found
        """
        if policy_name:
            policy = await self.get_policy_by_name(db, tenant_id, policy_name)
            if policy is None:
                raise RetentionPolicyNotFoundError(
                    f"Retention policy '{policy_name}' not found"
                )
            return policy

        # Use system default
        policy = await self.get_policy(db, SYSTEM_POLICY_DEFAULT)
        if policy is None:
            raise RetentionPolicyNotFoundError("System default policy not found")
        return policy

    async def get_system_policy(
        self,
        db: AsyncSession,
        name: str,
    ) -> RetentionPolicyModel | None:
        """Get a system policy by name.

        Args:
            db: Database session
            name: System policy name (default, zero-retention, keep)

        Returns:
            RetentionPolicyModel or None if not found
        """
        query = select(RetentionPolicyModel).where(
            RetentionPolicyModel.tenant_id.is_(None),
            RetentionPolicyModel.name == name,
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()
