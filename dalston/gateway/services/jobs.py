"""Job lifecycle management service."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.models import JobStatus
from dalston.db.models import JobModel


class JobsService:
    """Service for job CRUD operations."""

    async def create_job(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        audio_uri: str,
        parameters: dict[str, Any],
        webhook_url: str | None = None,
        webhook_metadata: dict | None = None,
    ) -> JobModel:
        """Create a new transcription job.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            audio_uri: S3 URI to uploaded audio
            parameters: Job configuration parameters
            webhook_url: Optional webhook URL for completion callback
            webhook_metadata: Optional custom data echoed in webhook callback

        Returns:
            Created JobModel instance
        """
        job = JobModel(
            tenant_id=tenant_id,
            audio_uri=audio_uri,
            parameters=parameters,
            webhook_url=webhook_url,
            webhook_metadata=webhook_metadata,
            status=JobStatus.PENDING.value,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job

    async def get_job(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None = None,
    ) -> JobModel | None:
        """Fetch a job by ID.

        Args:
            db: Database session
            job_id: Job UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            JobModel or None if not found
        """
        query = select(JobModel).where(JobModel.id == job_id)

        # Tenant isolation (when auth is enabled)
        if tenant_id is not None:
            query = query.where(JobModel.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        limit: int = 20,
        offset: int = 0,
        status: JobStatus | None = None,
    ) -> tuple[list[JobModel], int]:
        """List jobs for a tenant with pagination.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            limit: Maximum number of results
            offset: Number of results to skip
            status: Optional status filter

        Returns:
            Tuple of (jobs list, total count)
        """
        # Base query with tenant filter
        base_query = select(JobModel).where(JobModel.tenant_id == tenant_id)

        # Optional status filter
        if status is not None:
            base_query = base_query.where(JobModel.status == status.value)

        # Count total
        count_query = select(func.count()).select_from(base_query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar_one()

        # Fetch paginated results, ordered by created_at descending
        query = (
            base_query.order_by(JobModel.created_at.desc()).limit(limit).offset(offset)
        )
        result = await db.execute(query)
        jobs = list(result.scalars().all())

        return jobs, total

    async def update_job_status(
        self,
        db: AsyncSession,
        job_id: UUID,
        status: JobStatus,
        error: str | None = None,
    ) -> JobModel | None:
        """Update job status.

        Args:
            db: Database session
            job_id: Job UUID
            status: New status
            error: Optional error message (for failed status)

        Returns:
            Updated JobModel or None if not found
        """
        job = await db.get(JobModel, job_id)
        if job is None:
            return None

        job.status = status.value
        if error is not None:
            job.error = error

        await db.commit()
        await db.refresh(job)
        return job
