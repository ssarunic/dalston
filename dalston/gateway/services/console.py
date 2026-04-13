"""Console service for admin dashboard and metrics queries.

This service encapsulates all database queries for the admin console,
keeping SQL out of the API handlers.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dalston.common.models import JobStatus, TaskStatus
from dalston.common.utils import compute_duration_ms
from dalston.db.models import JobModel, TaskModel
from dalston.db.session import DEFAULT_TENANT_ID

# Canonical pipeline stage order, mirroring web/src/lib/stages.ts. Used by
# the queue board to render columns in pipeline order and to compute
# visible_stages / hidden_stages. Unknown stages sort after these.
PIPELINE_STAGES: tuple[str, ...] = (
    "prepare",
    "transcribe",
    "align",
    "diarize",
    "pii_detect",
    "audio_redact",
    "merge",
)


def _normalize_stage(stage: str) -> str:
    """Strip channel suffix (e.g. transcribe_ch0 -> transcribe).

    Mirrors DAGViewer.normalizeStage() in the web console so the frontend
    and backend agree on which stage a task belongs to.
    """
    if "_ch" in stage:
        base, _, suffix = stage.rpartition("_ch")
        if suffix.isdigit():
            return base
    return stage


@dataclass
class JobSummaryDTO:
    """Job summary for listings and dashboard."""

    id: UUID
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass
class DashboardStats:
    """Aggregated dashboard statistics."""

    status_counts: dict[str, int]
    completed_today: int
    failed_today: int
    recent_jobs: list[JobSummaryDTO]


@dataclass
class ThroughputBucket:
    """Hourly job throughput data."""

    hour: str  # ISO 8601 timestamp
    completed: int
    failed: int


@dataclass
class SuccessRateWindow:
    """Success rate for a time window."""

    window: str  # "1h" or "24h"
    total: int
    completed: int
    failed: int
    rate: float  # 0.0 - 1.0


@dataclass
class EngineTaskStats:
    """Per-engine task statistics."""

    engine_id: str
    stage: str
    completed: int
    failed: int
    avg_duration_ms: float | None
    p95_duration_ms: float | None


@dataclass
class JobListItemDTO:
    """Extended job summary for console job listing."""

    id: UUID
    status: str
    display_name: str | None
    model: str | None
    audio_uri: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    audio_duration_seconds: float | None
    result_language_code: str | None
    result_word_count: int | None
    result_segment_count: int | None
    result_speaker_count: int | None


@dataclass
class JobDetailDTO:
    """Detailed job response."""

    id: UUID
    status: str
    audio_uri: str | None
    parameters: dict | None
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass
class TaskDTO:
    """Task in the job pipeline."""

    id: UUID
    stage: str
    engine_id: str
    status: str
    required: bool
    dependencies: list[UUID]
    ready_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    retries: int
    max_retries: int
    error: str | None


@dataclass
class JobWithTasksDTO:
    """Job with eagerly loaded tasks."""

    id: UUID
    tasks: list[TaskDTO]


# ---------------------------------------------------------------------------
# Queue Board DTOs (M86)
# ---------------------------------------------------------------------------


@dataclass
class QueueBoardTaskDTO:
    """Task entry for the queue board view."""

    task_id: UUID
    job_id: UUID
    stage: str  # normalized (no _ch0/_ch1 suffix)
    status: str
    engine_id: str | None
    duration_ms: int | None
    wait_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


@dataclass
class QueueBoardJobDTO:
    """Active job entry for the queue board view."""

    job_id: UUID
    display_name: str | None
    status: str
    created_at: datetime
    audio_duration_seconds: float | None


@dataclass
class QueueBoardDTO:
    """Aggregated queue board data returned by the service layer.

    The tasks list is intentionally flat — the frontend regroups it for
    each of the three board layouts (Grid, Stage Board, Job Strips).
    Stage health and visible stages are computed from that same flat list.
    """

    jobs: list[QueueBoardJobDTO]
    tasks: list[QueueBoardTaskDTO]
    visible_stages: list[str]
    hidden_stages: list[str]
    completed_last_hour: int
    avg_pipeline_ms: float | None


class ConsoleService:
    """Service for admin console dashboard and metrics queries.

    Tenant filtering behavior varies by method:
    - Dashboard stats: Uses DEFAULT_TENANT_ID (single-tenant deployment)
    - Job listing: No tenant filter (admin cross-tenant view)
    - Job detail: Optional tenant filter for mixed admin/user access

    Authorization is enforced at the handler level via Permission.CONSOLE_ACCESS.
    """

    async def get_dashboard_stats(self, db: AsyncSession) -> DashboardStats:
        """Get aggregated dashboard statistics.

        Returns job counts by status, today's completed/failed counts,
        and the 5 most recent jobs.
        """
        # Job counts by status
        status_counts_result = await db.execute(
            select(JobModel.status, func.count(JobModel.id))
            .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
            .group_by(JobModel.status)
        )
        counts = {row[0]: row[1] for row in status_counts_result.all()}

        # Today's completed/failed counts
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        today_completed_result = await db.execute(
            select(func.count(JobModel.id))
            .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
            .where(JobModel.status == JobStatus.COMPLETED.value)
            .where(JobModel.completed_at >= today_start)
        )

        today_failed_result = await db.execute(
            select(func.count(JobModel.id))
            .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
            .where(JobModel.status == JobStatus.FAILED.value)
            .where(JobModel.completed_at >= today_start)
        )

        # Recent jobs
        recent_result = await db.execute(
            select(JobModel)
            .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
            .order_by(JobModel.created_at.desc())
            .limit(5)
        )

        recent_jobs = [
            JobSummaryDTO(
                id=job.id,
                status=job.status,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
            )
            for job in recent_result.scalars().all()
        ]

        return DashboardStats(
            status_counts=counts,
            completed_today=today_completed_result.scalar() or 0,
            failed_today=today_failed_result.scalar() or 0,
            recent_jobs=recent_jobs,
        )

    async def list_jobs_admin(
        self,
        db: AsyncSession,
        limit: int = 20,
        cursor: str | None = None,
        status: str | None = None,
        sort: Literal["created_desc", "created_asc"] = "created_desc",
    ) -> tuple[list[JobListItemDTO], str | None, bool]:
        """List all jobs without tenant filtering (admin view).

        Args:
            db: Database session
            limit: Maximum number of jobs to return
            cursor: Pagination cursor (created_at:id)
            status: Optional status filter
            sort: Sort order

        Returns:
            Tuple of (jobs, next_cursor, has_more)

        Raises:
            ValueError: If cursor format is invalid
        """
        query = select(JobModel)

        # Optional status filter
        if status:
            query = query.where(JobModel.status == status)

        # Apply cursor filter
        if cursor:
            cursor_created_at, cursor_id = self._decode_job_cursor(cursor)
            if sort == "created_asc":
                query = query.where(
                    (JobModel.created_at > cursor_created_at)
                    | (
                        (JobModel.created_at == cursor_created_at)
                        & (JobModel.id > cursor_id)
                    )
                )
            else:
                query = query.where(
                    (JobModel.created_at < cursor_created_at)
                    | (
                        (JobModel.created_at == cursor_created_at)
                        & (JobModel.id < cursor_id)
                    )
                )

        # Apply sorting and limit
        if sort == "created_asc":
            query = query.order_by(JobModel.created_at.asc(), JobModel.id.asc())
        else:
            query = query.order_by(JobModel.created_at.desc(), JobModel.id.desc())

        query = query.options(selectinload(JobModel.tasks)).limit(limit + 1)
        result = await db.execute(query)
        orm_jobs = list(result.scalars().unique().all())

        has_more = len(orm_jobs) > limit
        if has_more:
            orm_jobs = orm_jobs[:limit]

        # Convert to DTOs
        jobs = [
            JobListItemDTO(
                id=job.id,
                status=job.status,
                display_name=job.display_name,
                model=self._resolve_model_display(job),
                audio_uri=job.audio_uri,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                audio_duration_seconds=job.audio_duration,
                result_language_code=job.result_language_code,
                result_word_count=job.result_word_count,
                result_segment_count=job.result_segment_count,
                result_speaker_count=job.result_speaker_count,
            )
            for job in orm_jobs
        ]

        # Compute next cursor from last ORM job
        next_cursor = (
            self._encode_job_cursor(orm_jobs[-1]) if orm_jobs and has_more else None
        )

        return jobs, next_cursor, has_more

    async def get_job_admin(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> JobDetailDTO | None:
        """Get a job by ID without tenant filtering (admin view)."""
        result = await db.execute(select(JobModel).where(JobModel.id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            return None

        return JobDetailDTO(
            id=job.id,
            status=job.status,
            audio_uri=job.audio_uri,
            parameters=job.parameters,
            result=None,  # Results fetched from S3, not stored in DB
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    async def get_job_with_tasks_admin(
        self,
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None = None,
    ) -> JobWithTasksDTO | None:
        """Get a job with tasks eagerly loaded (admin view).

        Args:
            db: Database session
            job_id: Job ID
            tenant_id: Optional tenant filter (for non-admin use)
        """
        query = (
            select(JobModel)
            .where(JobModel.id == job_id)
            .options(selectinload(JobModel.tasks))
        )
        if tenant_id is not None:
            query = query.where(JobModel.tenant_id == tenant_id)

        result = await db.execute(query)
        job = result.scalar_one_or_none()
        if job is None:
            return None

        tasks = [
            TaskDTO(
                id=task.id,
                stage=task.stage,
                engine_id=task.engine_id,
                status=task.status,
                required=task.required,
                dependencies=task.dependencies or [],
                ready_at=task.ready_at,
                started_at=task.started_at,
                completed_at=task.completed_at,
                retries=task.retries,
                max_retries=task.max_retries,
                error=task.error,
            )
            for task in job.tasks
        ]

        return JobWithTasksDTO(id=job.id, tasks=tasks)

    async def get_hourly_throughput(
        self,
        db: AsyncSession,
        hours: int = 24,
    ) -> list[ThroughputBucket]:
        """Get hourly throughput for completed/failed jobs.

        Returns a full series with zeros for hours without data.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=hours)

        hour_col = func.date_trunc("hour", JobModel.completed_at)
        query = (
            select(
                hour_col.label("hour"),
                func.sum(
                    case(
                        (JobModel.status == JobStatus.COMPLETED.value, 1),
                        else_=0,
                    )
                ).label("completed"),
                func.sum(
                    case(
                        (JobModel.status == JobStatus.FAILED.value, 1),
                        else_=0,
                    )
                ).label("failed"),
            )
            .where(JobModel.completed_at >= cutoff)
            .where(
                JobModel.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value])
            )
            .group_by(hour_col)
            .order_by(hour_col)
        )
        rows = (await db.execute(query)).all()

        # Build lookup map
        bucket_map: dict[str, ThroughputBucket] = {}
        for row in rows:
            key = row.hour.isoformat()
            bucket_map[key] = ThroughputBucket(
                hour=key,
                completed=row.completed or 0,
                failed=row.failed or 0,
            )

        # Fill full series with zeros
        throughput: list[ThroughputBucket] = []
        for i in range(hours):
            bucket_start = (now - timedelta(hours=hours - 1 - i)).replace(
                minute=0, second=0, microsecond=0
            )
            key = bucket_start.isoformat()
            throughput.append(
                bucket_map.get(
                    key,
                    ThroughputBucket(hour=key, completed=0, failed=0),
                )
            )

        return throughput

    async def get_success_rates(
        self,
        db: AsyncSession,
    ) -> list[SuccessRateWindow]:
        """Get success rates for 1h and 24h windows."""
        now = datetime.now(UTC)
        windows = [
            ("1h", now - timedelta(hours=1)),
            ("24h", now - timedelta(hours=24)),
        ]

        results: list[SuccessRateWindow] = []
        for label, window_start in windows:
            query = (
                select(
                    func.count(JobModel.id).label("total"),
                    func.sum(
                        case(
                            (JobModel.status == JobStatus.COMPLETED.value, 1),
                            else_=0,
                        )
                    ).label("completed"),
                    func.sum(
                        case(
                            (JobModel.status == JobStatus.FAILED.value, 1),
                            else_=0,
                        )
                    ).label("failed"),
                )
                .where(JobModel.completed_at >= window_start)
                .where(
                    JobModel.status.in_(
                        [JobStatus.COMPLETED.value, JobStatus.FAILED.value]
                    )
                )
            )
            row = (await db.execute(query)).one()
            total = row.total or 0
            completed = row.completed or 0
            failed = row.failed or 0

            results.append(
                SuccessRateWindow(
                    window=label,
                    total=total,
                    completed=completed,
                    failed=failed,
                    rate=completed / total if total > 0 else 1.0,
                )
            )

        return results

    async def get_total_audio_minutes(self, db: AsyncSession) -> float:
        """Get total audio minutes processed (all time, completed jobs only)."""
        result = await db.execute(
            select(func.coalesce(func.sum(JobModel.audio_duration), 0.0)).where(
                JobModel.status == JobStatus.COMPLETED.value
            )
        )
        total_seconds = float(result.scalar() or 0)
        return total_seconds / 60.0

    async def get_total_jobs_count(self, db: AsyncSession) -> int:
        """Get total job count (all time)."""
        result = await db.execute(select(func.count(JobModel.id)))
        return result.scalar() or 0

    async def get_engine_task_stats(
        self,
        db: AsyncSession,
        engine_id: str,
        hours: int = 24,
    ) -> EngineTaskStats:
        """Get task statistics for a specific engine engine_id.

        Args:
            db: Database session
            engine_id: Engine engine_id identifier
            hours: Time window in hours

        Returns:
            Task statistics including completed/failed counts and latency metrics
        """
        from sqlalchemy import extract

        cutoff = datetime.now(UTC) - timedelta(hours=hours)

        query = (
            select(
                func.count(TaskModel.id)
                .filter(TaskModel.status == "completed")
                .label("completed"),
                func.count(TaskModel.id)
                .filter(TaskModel.status == "failed")
                .label("failed"),
                func.avg(
                    extract(
                        "epoch",
                        TaskModel.completed_at - TaskModel.started_at,
                    )
                )
                .filter(
                    TaskModel.status == "completed",
                    TaskModel.started_at.isnot(None),
                    TaskModel.completed_at.isnot(None),
                )
                .label("avg_seconds"),
                func.percentile_cont(0.95)
                .within_group(
                    extract(
                        "epoch",
                        TaskModel.completed_at - TaskModel.started_at,
                    )
                )
                .filter(
                    TaskModel.status == "completed",
                    TaskModel.started_at.isnot(None),
                    TaskModel.completed_at.isnot(None),
                )
                .label("p95_seconds"),
            )
            .where(TaskModel.engine_id == engine_id)
            .where(TaskModel.started_at >= cutoff)
        )
        row = (await db.execute(query)).one()

        return EngineTaskStats(
            engine_id=engine_id,
            stage="",  # Caller provides stage from catalog
            completed=row.completed or 0,
            failed=row.failed or 0,
            avg_duration_ms=(
                round(row.avg_seconds * 1000, 1)
                if row.avg_seconds is not None
                else None
            ),
            p95_duration_ms=(
                round(row.p95_seconds * 1000, 1)
                if row.p95_seconds is not None
                else None
            ),
        )

    async def get_queue_board(self, db: AsyncSession) -> QueueBoardDTO:
        """Fetch all active jobs + their tasks plus pipeline aggregates.

        Returns a flat task list (one entry per task, regardless of job)
        together with the set of jobs those tasks belong to, pipeline-level
        aggregates (last-hour completions, avg pipeline duration), and the
        visible / hidden stage sets. The frontend regroups the tasks for
        each of the three board layouts (Grid, Stage Board, Job Strips);
        keeping the shape flat keeps the regrouping cheap and orthogonal.

        The query eager-loads tasks via selectinload so this is a single
        round-trip regardless of how many active jobs exist.
        """
        # Active jobs with tasks eagerly loaded. A job is "active" if it is
        # pending or running — completed / failed / cancelled jobs live on
        # the batch jobs page.
        jobs_result = await db.execute(
            select(JobModel)
            .where(
                JobModel.status.in_(
                    [JobStatus.PENDING.value, JobStatus.RUNNING.value]
                )
            )
            .options(selectinload(JobModel.tasks))
            .order_by(JobModel.created_at.asc())
        )
        orm_jobs = list(jobs_result.scalars().unique().all())

        # Flat task list (normalized stage) and job list
        jobs_dto: list[QueueBoardJobDTO] = []
        tasks_dto: list[QueueBoardTaskDTO] = []

        for job in orm_jobs:
            jobs_dto.append(
                QueueBoardJobDTO(
                    job_id=job.id,
                    display_name=job.display_name or None,
                    status=job.status,
                    created_at=job.created_at,
                    audio_duration_seconds=job.audio_duration,
                )
            )

            for task in job.tasks:
                tasks_dto.append(
                    QueueBoardTaskDTO(
                        task_id=task.id,
                        job_id=job.id,
                        stage=_normalize_stage(task.stage),
                        status=task.status,
                        engine_id=task.engine_id or None,
                        duration_ms=compute_duration_ms(
                            task.started_at, task.completed_at
                        ),
                        wait_ms=compute_duration_ms(
                            task.ready_at, task.started_at
                        ),
                        started_at=task.started_at,
                        completed_at=task.completed_at,
                        error=task.error,
                    )
                )

        # visible_stages: stages with at least one non-skipped task in the
        # currently active jobs. hidden_stages: the complement within the
        # canonical pipeline list. We deliberately exclude skipped tasks
        # from visibility so stages that were decided against at DAG build
        # time (e.g. align when the transcriber has implicit alignment)
        # don't pull in an empty column.
        seen_stages: set[str] = set()
        for task in tasks_dto:
            if task.status != TaskStatus.SKIPPED.value:
                seen_stages.add(task.stage)

        visible_stages = [s for s in PIPELINE_STAGES if s in seen_stages]
        hidden_stages = [s for s in PIPELINE_STAGES if s not in seen_stages]

        # Last-hour completion stats — the summary cards on the page header
        # show "completed in the last hour" and average pipeline wall-clock.
        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        recent_query = select(
            func.count(JobModel.id).label("count"),
            func.avg(
                func.extract("epoch", JobModel.completed_at - JobModel.created_at)
            ).label("avg_seconds"),
        ).where(
            JobModel.status == JobStatus.COMPLETED.value,
            JobModel.completed_at >= one_hour_ago,
            JobModel.completed_at.isnot(None),
            JobModel.created_at.isnot(None),
        )
        recent_row = (await db.execute(recent_query)).one()
        completed_last_hour = int(recent_row.count or 0)
        avg_pipeline_ms = (
            round(recent_row.avg_seconds * 1000, 1)
            if recent_row.avg_seconds is not None
            else None
        )

        return QueueBoardDTO(
            jobs=jobs_dto,
            tasks=tasks_dto,
            visible_stages=visible_stages,
            hidden_stages=hidden_stages,
            completed_last_hour=completed_last_hour,
            avg_pipeline_ms=avg_pipeline_ms,
        )

    @staticmethod
    def _resolve_model_display(job: JobModel) -> str | None:
        """Resolve the model display string for a job.

        Precedence:
        1. Explicit model_transcribe parameter → use as-is
        2. Auto-selected via orchestrator → "Auto (<selected engine>)"
        3. Pending/running with no selection yet → "Auto (pending selection)"
        4. Fallback → "Auto"
        """
        # Explicit model selection.
        explicit = job.parameters.get("model_transcribe") if job.parameters else None
        if explicit:
            return explicit

        # Look for orchestrator-selected engine from transcribe task
        transcribe_task = None
        for task in job.tasks:
            if task.stage == "transcribe":
                transcribe_task = task
                break
        if transcribe_task is None:
            for task in job.tasks:
                if task.stage.startswith("transcribe_ch"):
                    transcribe_task = task
                    break

        if transcribe_task is not None:
            # Prefer loaded_model_id from task config
            loaded_model_id = (
                transcribe_task.config.get("loaded_model_id")
                if transcribe_task.config
                else None
            )
            if loaded_model_id:
                return f"Auto ({loaded_model_id})"
            if transcribe_task.engine_id:
                return f"Auto ({transcribe_task.engine_id})"

        # No transcribe task yet — check if job is still in progress
        if job.status in ("pending", "running"):
            return "Auto (pending selection)"

        return "Auto"

    def _encode_job_cursor(self, job: JobModel) -> str:
        """Encode a job into a pagination cursor."""
        return f"{job.created_at.isoformat()}:{job.id}"

    def _decode_job_cursor(self, cursor: str) -> tuple[datetime, UUID]:
        """Decode a pagination cursor into created_at and id.

        Raises:
            ValueError: If the cursor format is invalid
        """
        parts = cursor.rsplit(":", 1)
        if len(parts) != 2:
            raise ValueError("Invalid cursor format")
        created_at = datetime.fromisoformat(parts[0])
        job_id = UUID(parts[1])
        return created_at, job_id
