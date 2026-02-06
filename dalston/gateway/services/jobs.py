"""Job lifecycle management service."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dalston.common.models import JobStatus
from dalston.db.models import JobModel, TaskModel


class JobStats:
    """Job statistics for dashboard."""

    def __init__(
        self,
        running: int,
        queued: int,
        completed_today: int,
        failed_today: int,
    ):
        self.running = running
        self.queued = queued
        self.completed_today = completed_today
        self.failed_today = failed_today


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

    async def get_stats(
        self,
        db: AsyncSession,
        tenant_id: UUID | None = None,
    ) -> JobStats:
        """Get job statistics for dashboard.

        Args:
            db: Database session
            tenant_id: Optional tenant UUID for isolation (None = all tenants)

        Returns:
            JobStats with running, queued, completed_today, failed_today counts
        """

        # Base filter (optional tenant isolation)
        def base_filter(query):
            if tenant_id is not None:
                return query.where(JobModel.tenant_id == tenant_id)
            return query

        # Count running jobs
        running_query = base_filter(
            select(func.count())
            .select_from(JobModel)
            .where(JobModel.status == JobStatus.RUNNING.value)
        )
        running = (await db.execute(running_query)).scalar() or 0

        # Count queued (pending) jobs
        queued_query = base_filter(
            select(func.count())
            .select_from(JobModel)
            .where(JobModel.status == JobStatus.PENDING.value)
        )
        queued = (await db.execute(queued_query)).scalar() or 0

        # Today's start (UTC)
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Count completed today
        completed_query = base_filter(
            select(func.count())
            .select_from(JobModel)
            .where(
                JobModel.status == JobStatus.COMPLETED.value,
                JobModel.completed_at >= today_start,
            )
        )
        completed_today = (await db.execute(completed_query)).scalar() or 0

        # Count failed today
        failed_query = base_filter(
            select(func.count())
            .select_from(JobModel)
            .where(
                JobModel.status == JobStatus.FAILED.value,
                JobModel.completed_at >= today_start,
            )
        )
        failed_today = (await db.execute(failed_query)).scalar() or 0

        return JobStats(
            running=running,
            queued=queued,
            completed_today=completed_today,
            failed_today=failed_today,
        )

    async def get_job_with_tasks(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None = None,
    ) -> JobModel | None:
        """Fetch a job with its tasks eagerly loaded.

        Args:
            db: Database session
            job_id: Job UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            JobModel with tasks loaded, or None if not found
        """
        query = (
            select(JobModel)
            .options(selectinload(JobModel.tasks))
            .where(JobModel.id == job_id)
        )

        if tenant_id is not None:
            query = query.where(JobModel.tenant_id == tenant_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_job_tasks(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[TaskModel]:
        """Fetch all tasks for a job, ordered by dependency topology.

        Args:
            db: Database session
            job_id: Job UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            List of TaskModel ordered by execution sequence
        """
        # First verify job exists and belongs to tenant
        job = await self.get_job(db, job_id, tenant_id)
        if job is None:
            return []

        # Fetch tasks for this job
        query = select(TaskModel).where(TaskModel.job_id == job_id)
        result = await db.execute(query)
        tasks = list(result.scalars().all())

        # Topological sort by dependencies
        return self._topological_sort_tasks(tasks)

    async def get_task(
        self,
        db: AsyncSession,
        job_id: UUID,
        task_id: UUID,
        tenant_id: UUID | None = None,
    ) -> TaskModel | None:
        """Fetch a specific task, verifying it belongs to the job and tenant.

        Args:
            db: Database session
            job_id: Job UUID
            task_id: Task UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            TaskModel or None if not found or unauthorized
        """
        # First verify job exists and belongs to tenant
        job = await self.get_job(db, job_id, tenant_id)
        if job is None:
            return None

        # Fetch the task
        query = select(TaskModel).where(
            TaskModel.id == task_id,
            TaskModel.job_id == job_id,
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    def _topological_sort_tasks(self, tasks: list[TaskModel]) -> list[TaskModel]:
        """Sort tasks in topological order based on dependencies.

        Tasks at the same dependency level are sorted alphabetically by stage.
        """
        if not tasks:
            return []

        # Build lookup maps
        task_by_id = {task.id: task for task in tasks}
        in_degree = {task.id: 0 for task in tasks}

        # Calculate in-degrees
        for task in tasks:
            for dep_id in task.dependencies:
                if dep_id in task_by_id:
                    in_degree[task.id] += 1

        # Kahn's algorithm with alphabetical tie-breaking
        result = []
        ready = sorted(
            [t for t in tasks if in_degree[t.id] == 0],
            key=lambda t: t.stage,
        )

        while ready:
            # Take the first (alphabetically) ready task
            current = ready.pop(0)
            result.append(current)

            # Find tasks that depend on this one and reduce their in-degree
            next_ready = []
            for task in tasks:
                if current.id in task.dependencies:
                    in_degree[task.id] -= 1
                    if in_degree[task.id] == 0:
                        next_ready.append(task)

            # Sort new ready tasks and merge with existing ready list
            next_ready.sort(key=lambda t: t.stage)
            ready = sorted(ready + next_ready, key=lambda t: t.stage)

        return result
