"""Job lifecycle management service."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dalston.common.models import JobStatus, TaskStatus
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


@dataclass
class CancelResult:
    """Result of a job cancellation request."""

    job: JobModel
    status: JobStatus
    message: str
    running_task_count: int


class JobsService:
    """Service for job CRUD operations."""

    async def create_job(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID,
        audio_uri: str,
        parameters: dict[str, Any],
        webhook_url: str | None = None,
        webhook_metadata: dict | None = None,
        audio_format: str | None = None,
        audio_duration: float | None = None,
        audio_sample_rate: int | None = None,
        audio_channels: int | None = None,
        audio_bit_depth: int | None = None,
        # Retention: 0=transient, -1=permanent, N=days
        retention: int = 30,
        # PII fields (M26)
        pii_detection_enabled: bool = False,
        pii_detection_tier: str | None = None,
        pii_entity_types: list[str] | None = None,
        pii_redact_audio: bool = False,
        pii_redaction_mode: str | None = None,
    ) -> JobModel:
        """Create a new transcription job.

        Args:
            db: Database session
            job_id: Pre-generated job UUID (used for S3 path consistency)
            tenant_id: Tenant UUID for isolation
            audio_uri: S3 URI to uploaded audio
            parameters: Job configuration parameters
            webhook_url: Optional webhook URL for completion callback
            webhook_metadata: Optional custom data echoed in webhook callback
            audio_format: Audio codec/format (e.g., "mp3", "wav")
            audio_duration: Duration in seconds
            audio_sample_rate: Sample rate in Hz
            audio_channels: Number of audio channels
            audio_bit_depth: Bits per sample (e.g., 16, 24)
            retention: Retention in days (0=transient, -1=permanent, N=days)

        Returns:
            Created JobModel instance
        """
        job = JobModel(
            id=job_id,
            tenant_id=tenant_id,
            audio_uri=audio_uri,
            parameters=parameters,
            webhook_url=webhook_url,
            webhook_metadata=webhook_metadata,
            status=JobStatus.PENDING.value,
            audio_format=audio_format,
            audio_duration=audio_duration,
            audio_sample_rate=audio_sample_rate,
            audio_channels=audio_channels,
            audio_bit_depth=audio_bit_depth,
            retention=retention,
            pii_detection_enabled=pii_detection_enabled,
            pii_detection_tier=pii_detection_tier,
            pii_entity_types=pii_entity_types,
            pii_redact_audio=pii_redact_audio,
            pii_redaction_mode=pii_redaction_mode,
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
        cursor: str | None = None,
        status: JobStatus | None = None,
    ) -> tuple[list[JobModel], bool]:
        """List jobs for a tenant with cursor-based pagination.

        Args:
            db: Database session
            tenant_id: Tenant UUID for isolation
            limit: Maximum number of results
            cursor: Pagination cursor (format: created_at_iso:job_id)
            status: Optional status filter

        Returns:
            Tuple of (jobs list, has_more flag)
        """
        # Base query with tenant filter
        query = select(JobModel).where(JobModel.tenant_id == tenant_id)

        # Optional status filter
        if status is not None:
            query = query.where(JobModel.status == status.value)

        # Apply cursor filter if provided
        if cursor:
            cursor_created_at, cursor_id = self._decode_job_cursor(cursor)
            # Use composite cursor: created_at DESC, id DESC
            query = query.where(
                (JobModel.created_at < cursor_created_at)
                | (
                    (JobModel.created_at == cursor_created_at)
                    & (JobModel.id < cursor_id)
                )
            )

        # Fetch limit + 1 to determine has_more
        query = query.order_by(JobModel.created_at.desc(), JobModel.id.desc()).limit(
            limit + 1
        )

        result = await db.execute(query)
        jobs = list(result.scalars().all())

        # Check if there are more results
        has_more = len(jobs) > limit
        if has_more:
            jobs = jobs[:limit]

        return jobs, has_more

    def encode_job_cursor(self, job: JobModel) -> str:
        """Encode a job into a pagination cursor."""
        return f"{job.created_at.isoformat()}:{job.id}"

    def _decode_job_cursor(self, cursor: str) -> tuple[datetime, UUID]:
        """Decode a pagination cursor into (created_at, id)."""
        parts = cursor.rsplit(":", 1)
        if len(parts) != 2:
            raise ValueError("Invalid cursor format")
        created_at_str, id_str = parts
        created_at = datetime.fromisoformat(created_at_str)
        job_id = UUID(id_str)
        return created_at, job_id

    # Terminal states where deletion is allowed
    TERMINAL_STATES = {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }

    async def delete_job(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None = None,
    ) -> JobModel | None:
        """Delete a job record and its associated tasks.

        Only jobs in terminal states (completed, failed, cancelled) can be deleted.
        S3 artifact cleanup is the caller's responsibility.

        Args:
            db: Database session
            job_id: Job UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            The deleted JobModel (detached) or None if not found

        Raises:
            ValueError: If job is not in a terminal state
        """
        job = await self.get_job(db, job_id, tenant_id=tenant_id)
        if job is None:
            return None

        if job.status not in self.TERMINAL_STATES:
            raise ValueError(
                f"Cannot delete job in '{job.status}' state. "
                f"Only completed, failed, or cancelled jobs can be deleted."
            )

        # Explicitly delete tasks first (no CASCADE DELETE per CLAUDE.md)
        await db.execute(delete(TaskModel).where(TaskModel.job_id == job_id))
        await db.delete(job)
        await db.commit()
        return job

    # States that can be cancelled
    CANCELLABLE_STATES = {
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
    }

    async def cancel_job(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None = None,
    ) -> CancelResult | None:
        """Request cancellation of a pending or running job.

        Cancellation is "soft": running tasks complete naturally, only
        queued/pending tasks are cancelled. The orchestrator handles
        removing tasks from Redis queues.

        Args:
            db: Database session
            job_id: Job UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            CancelResult with job and status info, or None if not found

        Raises:
            ValueError: If job is not in a cancellable state
        """
        # Fetch job with tasks
        job = await self.get_job_with_tasks(db, job_id, tenant_id=tenant_id)
        if job is None:
            return None

        # Check if job can be cancelled
        if job.status not in self.CANCELLABLE_STATES:
            raise ValueError(
                f"Cannot cancel job in '{job.status}' state. "
                f"Only pending or running jobs can be cancelled."
            )

        # Count tasks by status
        running_count = 0
        pending_count = 0

        for task in job.tasks:
            if task.status == TaskStatus.RUNNING.value:
                running_count += 1
            elif task.status in (TaskStatus.PENDING.value, TaskStatus.READY.value):
                # Mark PENDING and READY tasks as CANCELLED
                task.status = TaskStatus.CANCELLED.value
                pending_count += 1

        # Determine new job status
        if running_count > 0:
            # Tasks still running - set to CANCELLING
            job.status = JobStatus.CANCELLING.value
            new_status = JobStatus.CANCELLING
            message = f"Cancellation requested. {running_count} task(s) still running."
        else:
            # No running tasks - can immediately set to CANCELLED
            job.status = JobStatus.CANCELLED.value
            job.completed_at = datetime.now(UTC)
            new_status = JobStatus.CANCELLED
            message = "Job cancelled."

        await db.commit()
        await db.refresh(job)

        return CancelResult(
            job=job,
            status=new_status,
            message=message,
            running_task_count=running_count,
        )

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
        """Fetch a job with its tasks and retention policy eagerly loaded.

        Args:
            db: Database session
            job_id: Job UUID
            tenant_id: Optional tenant UUID for isolation check

        Returns:
            JobModel with tasks and retention_policy loaded, or None if not found
        """
        query = (
            select(JobModel)
            .options(
                selectinload(JobModel.tasks),
                selectinload(JobModel.retention_policy),
            )
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
