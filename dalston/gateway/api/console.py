"""Console API endpoints for the web management interface.

GET /api/console/dashboard - Aggregated dashboard data
GET /api/console/jobs/{job_id}/tasks - Get task DAG for a job
GET /api/console/engines - Get batch and realtime engine status
DELETE /api/console/jobs/{job_id} - Delete a job and its artifacts (admin)
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dalston.common.events import publish_job_cancel_requested
from dalston.common.models import JobStatus
from dalston.config import Settings
from dalston.db.models import JobModel
from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.dependencies import (
    RequireAdmin,
    get_db,
    get_jobs_service,
    get_redis,
    get_session_router,
    get_settings,
)
from dalston.gateway.models.responses import JobCancelledResponse
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.storage import StorageService
from dalston.session_router import SessionRouter

logger = structlog.get_logger()

router = APIRouter(prefix="/api/console", tags=["console"])


# Dashboard models
class SystemStatus(BaseModel):
    """System health status."""

    healthy: bool
    version: str = "0.1.0"


class BatchStats(BaseModel):
    """Batch processing statistics."""

    running_jobs: int
    queued_jobs: int
    completed_today: int
    failed_today: int


class RealtimeCapacity(BaseModel):
    """Realtime worker capacity."""

    total_capacity: int
    used_capacity: int
    available_capacity: int
    worker_count: int
    ready_workers: int


class JobSummary(BaseModel):
    """Job summary for dashboard."""

    id: UUID
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class DashboardResponse(BaseModel):
    """Aggregated dashboard data."""

    system: SystemStatus
    batch: BatchStats
    realtime: RealtimeCapacity
    recent_jobs: list[JobSummary]


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Get dashboard data",
    description="Get aggregated dashboard data including system status, batch stats, realtime capacity, and recent jobs.",
)
async def get_dashboard(
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    session_router: SessionRouter = Depends(get_session_router),
) -> DashboardResponse:
    """Get aggregated dashboard data in a single call."""
    # Get job counts by status
    status_counts = await db.execute(
        select(JobModel.status, func.count(JobModel.id))
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .group_by(JobModel.status)
    )
    counts = {row[0]: row[1] for row in status_counts.all()}

    # Get today's completed/failed counts
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = await db.execute(
        select(func.count(JobModel.id))
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .where(JobModel.status == JobStatus.COMPLETED.value)
        .where(JobModel.completed_at >= today_start)
    )
    today_failed = await db.execute(
        select(func.count(JobModel.id))
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .where(JobModel.status == JobStatus.FAILED.value)
        .where(JobModel.completed_at >= today_start)
    )

    # Get recent jobs
    recent_result = await db.execute(
        select(JobModel)
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .order_by(JobModel.created_at.desc())
        .limit(5)
    )
    recent_jobs = recent_result.scalars().all()

    # Get realtime capacity
    try:
        capacity = await session_router.get_capacity()
        realtime = RealtimeCapacity(
            total_capacity=capacity.total_capacity,
            used_capacity=capacity.used_capacity,
            available_capacity=capacity.available_capacity,
            worker_count=capacity.worker_count,
            ready_workers=capacity.ready_workers,
        )
    except Exception:
        # If session router is not available, return zeros
        realtime = RealtimeCapacity(
            total_capacity=0,
            used_capacity=0,
            available_capacity=0,
            worker_count=0,
            ready_workers=0,
        )

    return DashboardResponse(
        system=SystemStatus(healthy=True),
        batch=BatchStats(
            running_jobs=counts.get(JobStatus.RUNNING.value, 0),
            queued_jobs=counts.get(JobStatus.PENDING.value, 0),
            completed_today=today_completed.scalar() or 0,
            failed_today=today_failed.scalar() or 0,
        ),
        realtime=realtime,
        recent_jobs=[
            JobSummary(
                id=job.id,
                status=job.status,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
            )
            for job in recent_jobs
        ],
    )


class TaskResponse(BaseModel):
    """Task in the job pipeline."""

    id: UUID
    stage: str
    engine_id: str
    status: str
    dependencies: list[UUID]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Response for task list endpoint."""

    job_id: UUID
    tasks: list[TaskResponse]


@router.get(
    "/jobs/{job_id}/tasks",
    response_model=TaskListResponse,
    summary="Get job tasks",
    description="Get all tasks in the job's processing pipeline.",
)
async def get_job_tasks(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    """Get task DAG for a job."""
    # Fetch job with tasks
    result = await db.execute(
        select(JobModel)
        .where(JobModel.id == job_id)
        .where(JobModel.tenant_id == DEFAULT_TENANT_ID)
        .options(selectinload(JobModel.tasks))
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Sort tasks by stage order for display
    stage_order = {
        "prepare": 0,
        "transcribe": 1,
        "align": 2,
        "diarize": 3,
        "detect": 4,
        "refine": 5,
        "merge": 6,
    }
    sorted_tasks = sorted(
        job.tasks,
        key=lambda t: (stage_order.get(t.stage, 99), t.engine_id),
    )

    return TaskListResponse(
        job_id=job.id,
        tasks=[
            TaskResponse(
                id=task.id,
                stage=task.stage,
                engine_id=task.engine_id,
                status=task.status,
                dependencies=task.dependencies or [],
                started_at=task.started_at,
                completed_at=task.completed_at,
                error=task.error,
            )
            for task in sorted_tasks
        ],
    )


class TaskArtifactResponse(BaseModel):
    """Task artifact data for debugging."""

    task_id: UUID
    job_id: UUID
    stage: str
    engine_id: str
    status: str
    required: bool
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    retries: int = 0
    max_retries: int = 2
    error: str | None = None
    dependencies: list[UUID] = []
    input: dict | None = None
    output: dict | None = None


@router.get(
    "/jobs/{job_id}/tasks/{task_id}/artifacts",
    response_model=TaskArtifactResponse,
    summary="Get task artifacts",
    description="Get detailed task information including input/output artifacts.",
)
async def get_task_artifacts(
    job_id: UUID,
    task_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> TaskArtifactResponse:
    """Get task artifacts for debugging."""
    from dalston.config import get_settings
    from dalston.gateway.services.storage import StorageService

    # Fetch job with tasks
    result = await db.execute(
        select(JobModel)
        .where(JobModel.id == job_id)
        .options(selectinload(JobModel.tasks))
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Find the task
    task = next((t for t in job.tasks if t.id == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Calculate duration
    duration_ms = None
    if task.started_at and task.completed_at:
        delta = task.completed_at - task.started_at
        duration_ms = int(delta.total_seconds() * 1000)

    # Fetch artifacts from S3 if task has started
    input_data = None
    output_data = None
    if task.status != "pending":
        settings = get_settings()
        storage = StorageService(settings)
        input_data = await storage.get_task_input(job_id, task_id)
        output_data = await storage.get_task_output(job_id, task_id)

    return TaskArtifactResponse(
        task_id=task.id,
        job_id=job_id,
        stage=task.stage,
        engine_id=task.engine_id,
        status=task.status,
        required=task.required,
        started_at=task.started_at,
        completed_at=task.completed_at,
        duration_ms=duration_ms,
        retries=task.retries,
        max_retries=task.max_retries,
        error=task.error,
        dependencies=task.dependencies or [],
        input=input_data,
        output=output_data,
    )


# Engine models
class BatchEngine(BaseModel):
    """Batch engine status."""

    engine_id: str
    stage: str
    status: str  # "healthy" or "unhealthy"
    queue_depth: int
    processing: int


class RealtimeWorker(BaseModel):
    """Realtime worker status."""

    worker_id: str
    endpoint: str
    status: str
    capacity: int
    active_sessions: int
    models: list[str]
    languages: list[str]


class EnginesResponse(BaseModel):
    """Response for engines endpoint."""

    batch_engines: list[BatchEngine]
    realtime_engines: list[RealtimeWorker]


# Known batch engines and their stages
BATCH_ENGINES = {
    "audio-prepare": "prepare",
    "faster-whisper": "transcribe",
    "parakeet": "transcribe",
    "whisperx-align": "align",
    "pyannote-3.1": "diarize",
    "pyannote-4.0": "diarize",
    "final-merger": "merge",
}


@router.get(
    "/engines",
    response_model=EnginesResponse,
    summary="Get engine status",
    description="Get status of all batch and realtime engines.",
)
async def get_engines(
    api_key: RequireAdmin,
    redis: Redis = Depends(get_redis),
    session_router: SessionRouter = Depends(get_session_router),
) -> EnginesResponse:
    """Get status of all engines."""
    # Get batch engine queue depths
    batch_engines = []
    for engine_id, stage in BATCH_ENGINES.items():
        queue_key = f"dalston:queue:{engine_id}"
        queue_depth = await redis.llen(queue_key) or 0

        batch_engines.append(
            BatchEngine(
                engine_id=engine_id,
                stage=stage,
                status="healthy",  # We assume healthy if queue is accessible
                queue_depth=queue_depth,
                processing=0,  # Not tracked currently
            )
        )

    # Get realtime workers
    realtime_engines = []
    try:
        workers = await session_router.list_workers()
        for worker in workers:
            realtime_engines.append(
                RealtimeWorker(
                    worker_id=worker.worker_id,
                    endpoint=worker.endpoint,
                    status=worker.status,
                    capacity=worker.capacity,
                    active_sessions=worker.active_sessions,
                    models=worker.models,
                    languages=worker.languages,
                )
            )
    except Exception:
        # Session router may not be available
        pass

    return EnginesResponse(
        batch_engines=batch_engines,
        realtime_engines=realtime_engines,
    )


# Job listing for console
class ConsoleJobSummary(BaseModel):
    """Job summary for console listing."""

    id: UUID
    status: str
    audio_uri: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Result stats (populated on completion)
    audio_duration_seconds: float | None = None
    result_language_code: str | None = None
    result_word_count: int | None = None
    result_segment_count: int | None = None
    result_speaker_count: int | None = None

    model_config = {"from_attributes": True}


class ConsoleJobListResponse(BaseModel):
    """Response for console job listing."""

    jobs: list[ConsoleJobSummary]
    total: int
    limit: int
    offset: int


@router.get(
    "/jobs",
    response_model=ConsoleJobListResponse,
    summary="List all jobs",
    description="List all jobs across all tenants (admin only).",
)
async def list_console_jobs(
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
) -> ConsoleJobListResponse:
    """List all jobs for console (admin view)."""
    # Build query - no tenant filter for admin
    query = select(JobModel).order_by(JobModel.created_at.desc())

    # Optional status filter
    if status:
        query = query.where(JobModel.status == status)

    # Get total count
    count_query = select(func.count(JobModel.id))
    if status:
        count_query = count_query.where(JobModel.status == status)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    jobs = result.scalars().all()

    return ConsoleJobListResponse(
        jobs=[
            ConsoleJobSummary(
                id=job.id,
                status=job.status,
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
            for job in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


class ConsoleJobDetailResponse(BaseModel):
    """Detailed job response for console."""

    id: UUID
    status: str
    audio_uri: str | None = None
    parameters: dict | None = None
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.get(
    "/jobs/{job_id}",
    response_model=ConsoleJobDetailResponse,
    summary="Get job details",
    description="Get detailed job information (admin only).",
)
async def get_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
) -> ConsoleJobDetailResponse:
    """Get job details for console (admin view)."""
    result = await db.execute(select(JobModel).where(JobModel.id == job_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return ConsoleJobDetailResponse(
        id=job.id,
        status=job.status,
        audio_uri=job.audio_uri,
        parameters=job.parameters,
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    summary="Delete a job",
    description="Delete a job and its artifacts. Only terminal-state jobs can be deleted. Admin only.",
    responses={
        204: {"description": "Job deleted successfully"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a terminal state"},
    },
)
async def delete_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> Response:
    """Delete a job and all associated artifacts (admin endpoint).

    No tenant filter — admins can delete any job.
    """
    try:
        job = await jobs_service.delete_job(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Clean up S3 artifacts (best-effort)
    try:
        storage = StorageService(settings)
        await storage.delete_job_artifacts(job_id)
    except Exception:
        logger.warning(
            "Failed to delete S3 artifacts for job %s", job_id, exc_info=True
        )

    return Response(status_code=204)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobCancelledResponse,
    summary="Cancel a job",
    description="Cancel a pending or running job. Running tasks complete naturally. Admin only.",
    responses={
        200: {"description": "Cancellation requested"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not in a cancellable state"},
    },
)
async def cancel_console_job(
    job_id: UUID,
    api_key: RequireAdmin,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    jobs_service: JobsService = Depends(get_jobs_service),
) -> JobCancelledResponse:
    """Cancel a job (admin endpoint).

    No tenant filter — admins can cancel any job.
    """
    try:
        result = await jobs_service.cancel_job(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Publish event for orchestrator
    await publish_job_cancel_requested(redis, job_id)

    return JobCancelledResponse(
        id=result.job.id,
        status=result.status,
        message=result.message,
    )
